import logging

from taiga.utils import Client, status_mappings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)


def is_tested_entry(entry: dict):
    if "custom_attributes" not in entry["diff"]:
        return False

    custom_attributes = entry["diff"].get("custom_attributes")
    if not custom_attributes[1]:
        return False

    checkbox = [
        attribute for attribute in custom_attributes[1] if attribute["id"] == 44202
    ]
    if not checkbox:
        return False

    return checkbox[0]["value"]


def move_column(client: Client, old_status, new_status):
    stories = client.list_stories(status=status_mappings[old_status])

    for story in stories:
        client.update_story(
            story["id"],
            story["version"],
            status=status_mappings[new_status],
        )


def simple_move_column():
    client = Client()
    client.auth()

    move_column(client, "in-test", "awaiting-release")
