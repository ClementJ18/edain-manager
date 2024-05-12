import logging
import sys

from taiga.utils import Client, status_mappings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)

if __name__ == "__main__":
    client = Client()
    client.auth()

    is_beta = sys.argv[1].lower() == "beta"
    name = sys.argv[2:]

    if is_beta:
        new_status = status_mappings["in-test"]
        stories = client.list_stories(status=status_mappings["fixed-internally"])

        for story in stories:
            client.update_story(story["id"], story["version"], status=new_status)

    client.create_tag(name, "#4C566A")
