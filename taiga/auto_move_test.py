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


def auto_move_test():
    client = Client()
    client.auth()

    stories = client.list_stories(status=status_mappings["in-test"])

    for story in stories:
        attributes = client.get_story_attributes(story["id"])
        if attributes["attributes_values"].get("44202", False):
            history = client.get_issue_history(story["id"])
            diff = next(entry for entry in history if is_tested_entry(entry))
            client.update_story(
                story["id"],
                story["version"],
                status=status_mappings["awaiting-release"],
                comment=f"Tested by **{diff['user']['name']}**",
            )
