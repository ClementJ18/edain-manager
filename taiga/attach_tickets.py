import logging

from taiga.utils import Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)

if __name__ == "__main__":
    client = Client()
    client.auth()

    epics = client.list_epics()
    for version_tag in ["release", "beta"]:
        epic = next(
            epic
            for epic in epics
            if epic["status_extra_info"]["name"] == "Current"
            and version_tag in epic["subject"].lower()
        )
        issues = client.list_stories(tags=[version_tag])
        for issue in issues:
            client.update_story(
                issue["id"],
                issue["version"],
                tags=[tag for tag in issue["tags"] if tag[0] != version_tag],
            )
            client.attach_issue_to_epic(epic["id"], issue["id"])
