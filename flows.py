import datetime
import logging
import pathlib
import threading
import traceback

import requests
from flask_discord.models import User

from taiga.config import EPIC_STATUS_MAPPING, TAIGA_WEBHOOK
from taiga.move_column import move_column
from taiga.utils import REQUEST_TIMEOUT, Client, status_mappings

# In-process lock, so it only serialises flows while the app runs as a single
# process. Production pins `gunicorn --workers 1` for this reason: with more
# workers each gets its own lock and two flows could move the same tickets at
# once. See the Production section of the README.
flow_lock = threading.Lock()
RELEASE_LOG_FILE = "release_log.txt"
BUG_REPORT_FILE = "report.txt"


def generate_bug_list(client: Client, version):
    stories = client.list_stories(status=status_mappings["awaiting-release"])
    with open(BUG_REPORT_FILE, "w") as f:
        f.write(
            f"Bugs Fixed in Version {version}\n"
            + "\n".join([story["subject"] for story in stories])
        )


def log_line(string):
    with open(RELEASE_LOG_FILE, "a+") as f:
        f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {string}\n")

    logging.info(string)


def pre_flow():
    """Drop the previous run's log so the progress page only shows this one."""
    pathlib.Path(RELEASE_LOG_FILE).unlink(missing_ok=True)


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


def version_name(is_beta: bool, version: str, candidate: str) -> str:
    version_tag = "Beta" if is_beta else "Release"
    return f"{version} {version_tag}{' ' + candidate if is_beta else ''}"


def post_flow(is_beta: bool, version: str, candidate: str, user: User):
    log_line("Sending webhook")
    name = version_name(is_beta, version, candidate)
    data = {
        "content": None,
        "embeds": [
            {
                "title": "Board Updated!",
                "description": f"Ordered by **{user.username}**\nThe Taiga board is ready for **{name}**!",
                "color": 5814783,
                "fields": [],
            }
        ],
        "username": "Edain Manager",
        "attachments": [],
    }

    requests.post(TAIGA_WEBHOOK, json=data, timeout=REQUEST_TIMEOUT)


def error_flow(is_beta: bool, version: str, candidate: str, error: Exception):
    name = version_name(is_beta, version, candidate)
    traceback_string = "\n".join(
        traceback.format_exception(type(error), error, error.__traceback__, chain=True)
    )
    data = {
        "content": None,
        "embeds": [
            {
                "title": "Error!",
                "description": f"Failed to update the board for **{name}**\n```py\n{traceback_string}\n```",
                "color": 5814783,
                "fields": [],
            }
        ],
        "username": "Edain Manager",
        "attachments": [],
    }

    logging.exception("Failed to update the board for %s", name)
    requests.post(TAIGA_WEBHOOK, json=data, timeout=REQUEST_TIMEOUT)


def run_flows(is_beta: bool, version: str, candidate: str, user: User):
    # Non-blocking: the caller already rejected the request if a flow was running,
    # so a failure here means we lost a race and must not silently queue a flow.
    if not flow_lock.acquire(blocking=False):
        logging.warning(
            "Refusing concurrent flow for %s, one is already running", version
        )
        return

    try:
        _run_flows(is_beta, version, candidate, user)
    except Exception as e:
        error_flow(is_beta, version, candidate, e)
    finally:
        flow_lock.release()


def _run_flows(is_beta: bool, version: str, candidate: str, user: User):
    log_line("Starting taiga process...")
    pre_flow()

    log_line("Running taiga flow")
    taiga_flow(is_beta, version, candidate)

    post_flow(is_beta, version, candidate, user)
    log_line("Done taiga process...")
