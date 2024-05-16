import logging

from utils import Client, status_mappings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
)

def sort_tags(story):
    valid_tags = [x[0] for x in story["tags"] if x[0] not in ["release", "beta"]]
    if not valid_tags:
        return "null"
    
    return valid_tags[0]

if __name__ == "__main__":
    logging.info("Starting sorting")

    client = Client()
    client.auth()

    for status in status_mappings.values():
        stories = client.list_stories(status=status)
        stories.sort(key=sort_tags)
        client.bulk_order_stories([story["id"] for story in stories], status)
    
    logging.info("Done sorting")
