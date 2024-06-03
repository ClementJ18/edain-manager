import csv
import datetime
import logging
import os
import zipfile
import threading
import traceback

import git
import pandas
import pyBIG
import requests
from boto3.session import Session
from flask_discord.models import User

from taiga.config import (
    BUCKET_NAME,
    BUCKET_REGION,
    EPIC_STATUS_MAPPING,
    REMOTE_URL,
    REPO_PATH,
    SPACES_KEY,
    SPACES_SECRET,
    TAIGA_WEBHOOK,
)
from taiga.move_column import move_column
from taiga.utils import Client, status_mappings

build_lock = threading.Lock()
RELEASE_LOG_FILE = "release_log.txt"
REPO = git.Repo(REPO_PATH)

try:
    origin = REPO.remote("origin")
    if origin.url != REMOTE_URL:
        origin.set_url(REMOTE_URL)
except ValueError:
    REPO.create_remote("origin", REMOTE_URL)

def write_string_files(data, file, columns):
    with open(file, "w+", encoding="latin-1") as f:
        strings = [
            f'{row[0]}\n"{row[1]}"\nEND'
            for row in data[columns].itertuples(index=False)
        ]
        f.write("\n\n".join(strings))


def generate_bug_list(client: Client, version):
    stories = client.list_stories(status=status_mappings["awaiting-release"])
    with open("report.txt", "w") as f:
        f.write(
            f"Bugs Fixed in Version {version}\n"
            + "\n".join([story["subject"] for story in stories])
        )


def log_line(string):
    with open(RELEASE_LOG_FILE, "a+") as f:
        f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {string}\n")


def pre_flow(is_beta: bool, version: str, candidate: str, branch: str, user: dict):
    try:
        os.remove(RELEASE_LOG_FILE)
    except Exception:
        pass


def build_flow(is_beta: bool, version: str, candidate: str, checkout_target: str):
    # checkout branch and pull
    log_line(f"Checking out {checkout_target}")
    REPO.git.checkout(checkout_target)

    # rebuild strings
    log_line("Building strings")
    file_path = os.path.join(REPO_PATH, "_mod/Lotr.csv")
    data = pandas.read_csv(
        file_path, delimiter=";", keep_default_na=False, encoding="latin-1"
    )
    write_string_files(
        data,
        os.path.join(REPO_PATH, "_mod/_german/data/lotr.str"),
        ["Object Name", "German Description"],
    )
    write_string_files(
        data,
        os.path.join(REPO_PATH, "_mod/_english/data/lotr.str"),
        ["Object Name", "English Description"],
    )

    # rebuild asset?

    # build .big files
    log_line("Building big files")
    config = os.path.join(
        REPO_PATH, "tools/mod-starter/assets/final_big_builder_config.csv"
    )
    final_folder = os.path.join(REPO_PATH, "final_files")
    with open(config, newline="") as csvfile:
        csv_reader = csv.DictReader(csvfile, delimiter=";")

        for row in csv_reader:
            archive = pyBIG.Archive.empty()

            mod_path = os.path.join(REPO_PATH, row["Path"][1:])
            for content_path in row["Content"].strip().split(" "):
                object_path = os.path.join(mod_path, content_path)
                if os.path.isfile(object_path):
                    with open(object_path, "rb") as f:
                        archive.add_file(content_path.replace("/", "\\"), f.read())
                elif os.path.isdir(object_path):
                    for root, _, files in os.walk(object_path, topdown=False):
                        for name in files:
                            full_path = os.path.join(root, name)
                            file_name = full_path.replace(mod_path, "").replace(
                                "/", "\\"
                            )
                            with open(full_path, "rb") as f:
                                try:
                                    archive.add_file(
                                        (
                                            file_name[1:]
                                            if file_name.startswith("\\")
                                            else file_name
                                        ),
                                        f.read(),
                                    )
                                except KeyError:
                                    pass

            archive.save(os.path.join(final_folder, row["Name"]))
            log_line(f"Built {row['Name']}")

    # package big files together
    log_line("Zipping and uploading release")
    version_type = "beta" if is_beta else "release"
    archive_name = f"{version_type}_{version}{'_' + candidate if is_beta else ''}.zip"
    archive_path = os.path.join(REPO_PATH, archive_name)
    asset_path = os.path.join(REPO_PATH, "complete_asset", "asset.dat")
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for file in os.listdir(final_folder):
            archive.write(os.path.join(final_folder, file), arcname=file)

        archive.write(asset_path, arcname="asset.dat")

    # upload to storage
    session = Session()
    client = session.client(
        "s3",
        region_name=BUCKET_REGION,
        endpoint_url=f"https://{BUCKET_REGION}.digitaloceanspaces.com",
        aws_access_key_id=SPACES_KEY,
        aws_secret_access_key=SPACES_SECRET,
    )

    client.upload_file(archive_path, BUCKET_NAME, archive_name)
    os.remove(archive_path)

    log_line("Zipping and uploading individual files")
    for file in os.listdir(final_folder):
        if not file.endswith(".big"):
            continue

        path = os.path.join(final_folder, file)
        archive_name = f"{path}.zip"
        with zipfile.ZipFile(
            archive_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            archive.write(path, arcname=file)

        client.upload_file(archive_name, BUCKET_NAME, f"{version_type}/{file}.zip")
        os.remove(path)
        os.remove(archive_name)
        log_line(f"Zipped and uploaded {file}")

    asset_archive_path = os.path.join(final_folder, "asset.zip")
    with zipfile.ZipFile(
        asset_archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        archive.write(asset_path, arcname="asset.dat")

    client.upload_file(asset_archive_path, BUCKET_NAME, f"{version_type}/asset.zip")
    os.remove(asset_archive_path)


def taiga_flow(is_beta: bool, version: str, candidate: str):
    client = Client()
    client.auth()

    # mark previous epic as old
    epics = client.list_epics()

    version_tag = "beta" if is_beta else "release"
    name = f"{version} {version_tag.title()}{' ' + candidate if is_beta else ''} Bugs"

    # no need to recreate an existing epic
    log_line("Closing previous epic and creating new one")
    if not any(name in epic["subject"] for epic in epics):
        try:
            epic = next(
                epic
                for epic in epics
                if epic["status_extra_info"]["name"] == "Current"
                and version_tag in epic["subject"].lower()
            )

            client.update_epic(
                epic["id"],
                epic["version"],
                status=EPIC_STATUS_MAPPING["old"],
                order="3",
            )
        except StopIteration:
            logging.error("Could not close previous epic for %s", version_tag)

        # make new epic
        client.create_epic(name, status=EPIC_STATUS_MAPPING["current"])
    else:
        logging.info(
            "Skipping epic creation because duplicate already exists for %s", name
        )

    if is_beta:
        log_line("Moving tickets from fixed-internally to in-test")
        move_column(client, "fixed-internally", "in-test")
    else:
        # generate bug list
        log_line("Moving tickets from awaiting-release to done")
        generate_bug_list(client, version)
        move_column(client, "awaiting-release", "done")


def post_flow(is_beta: bool, version: str, candidate: str, branch: str, user: User):
    log_line("Sending webhook")
    version_tag = "Beta" if is_beta else "Release"
    name = f"{version} {version_tag}{' ' + candidate if is_beta else ''}"
    data = {
        "content": None,
        "embeds": [
            {
                "title": "Release Ready!",
                "description": f"Ordered by **{user.username}**\n**{name}** is ready!",
                "color": 5814783,
                "fields": [],
            }
        ],
        "username": "Edain Manager",
        "attachments": [],
    }

    requests.post(TAIGA_WEBHOOK, json=data)


def error_flow(is_beta: bool, version: str, candidate: str, error: Exception):
    version_tag = "Beta" if is_beta else "Release"
    name = f"{version} {version_tag}{' ' + candidate if is_beta else ''}"
    traceback_string = "\n".join(traceback.format_exception(error, chain=True))
    data = {
        "content": None,
        "embeds": [
            {
                "title": "Error!",
                "description": f"Failed to build **{name}**\n```py\n{traceback_string}\n```",
                "color": 5814783,
                "fields": [],
            }
        ],
        "username": "Edain Manager",
        "attachments": [],
    }

    logging.exception("Failed to build %s", name)
    requests.post(TAIGA_WEBHOOK, json=data)


def run_flows(
    is_beta: bool,
    version: str,
    candidate: str,
    user: User,
    flows: dict,
    branch: str,
    commit: str,
):

    with build_lock:
        try:
            _run_flows(is_beta, version, candidate, user, flows, branch, commit)
        except Exception as e:
            error_flow(is_beta, version, candidate, e)


def _run_flows(
    is_beta: bool,
    version: str,
    candidate: str,
    user: User,
    flows: dict,
    branch: str,
    commit: str,
):
    log_line("Starting release process...")
    pre_flow(is_beta, version, candidate, branch, user)

    log_line("Running selected flows")
    if flows["build"]:
        log_line("Running build flow")
        build_flow(is_beta, version, candidate, commit or branch)
    else:
        log_line("Skipping build flow")

    if flows["taiga"]:
        log_line("Running taiga flow")
        taiga_flow(is_beta, version, candidate)
    else:
        log_line("Skipping taiga flow")

    post_flow(is_beta, version, candidate, branch, user)
