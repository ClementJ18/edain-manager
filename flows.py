import csv
import logging
import os
import tarfile

import git
import pandas
import pyBIG
from boto3.session import Session

from taiga.config import (
    BUCKET_NAME,
    BUCKET_REGION,
    REPO_PATH,
    SPACES_KEY,
    SPACES_SECRET,
)
from taiga.move_column import move_column
from taiga.utils import Client, status_mappings

logging.basicConfig(level=logging.ERROR)


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


def pre_flow(is_beta: bool, version: str, candidate: str, branch: str):
    pass


def build_flow(is_beta: bool, version: str, candidate: str, branch: str):
    # checkout branch and pull
    repo = git.Repo(REPO_PATH)
    repo.git.checkout(branch)

    # rebuild strings
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
    config = os.path.join(
        REPO_PATH, "tools/mod-starter/assets/final_big_builder_config.csv"
    )
    final_folder = os.path.join(REPO_PATH, "final_files")
    with open(config, newline="") as csvfile:
        csv_reader = csv.DictReader(csvfile, delimiter=";")

        for row in csv_reader:
            archive = pyBIG.Archive()

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
                            )[1:]
                            with open(full_path, "rb") as f:
                                try:
                                    archive.add_file(file_name, f.read())
                                except KeyError:
                                    pass

            archive.save(os.path.join(final_folder, row["Name"]))

    # package big files together?
    version_type = "beta" if is_beta else "release"
    archive_name = f"{version_type}_{version}{'_' + candidate if is_beta else ''}"
    with tarfile.open(os.path.join(REPO_PATH, archive_name), "w") as archive:
        for file in os.listdir(final_folder):
            archive.add(os.path.join(final_folder, file))

    # upload to storage
    session = Session()
    client = session.client(
        "s3",
        region_name=BUCKET_REGION,
        endpoint_url=f"https://{BUCKET_REGION}.digitaloceanspaces.com",
        aws_access_key_id=SPACES_KEY,
        aws_secret_access_key=SPACES_SECRET,
    )

    for file in os.listdir(final_folder):
        client.upload_file(
            os.path.join(final_folder, version_type, file), BUCKET_NAME, file
        )

    client.upload_file(os.path.join(REPO_PATH, archive_name), BUCKET_NAME, archive_name)


def taiga_flow(is_beta: bool, version: str, candidate: str):
    client = Client()
    client.auth()

    # mark previous epic as old
    epics = client.list_epics()

    version_tag = "beta" if is_beta else "release"
    try:
        epic = next(
            epic
            for epic in epics
            if epic["status_extra_info"]["name"] == "Current"
            and version_tag in epic["subject"].lower()
        )

        client.update_epic(epic["id"], epic["version"], status="old")
    except StopIteration:
        logging.error("Could no close previous epic for %s", version_tag)

    # make new epic
    name = f"{version} {version_tag.title()}{' ' + candidate if is_beta else ''} Bugs"
    new_epic = client.create_epic(name)

    client.update_epic(new_epic["id"], new_epic["version"], status="current")

    if is_beta:
        move_column(client, "fixed-internally", "in-test")
    else:
        # generate bug list
        generate_bug_list(client, version)
        move_column(client, "awaiting-release", "done")


def post_flow(is_beta: bool, version: str, candidate: str, branch: str):
    pass


def run_flows(
    is_beta: bool, version: str, candidate: str = None, branch: str = "origin/main"
):
    pre_flow(is_beta, version, candidate, branch)

    build_flow(is_beta, version, candidate, branch)
    taiga_flow(is_beta, version, candidate)

    post_flow(is_beta, version, candidate, branch)
