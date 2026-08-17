from taiga.utils import Client, status_mappings


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

    move_column(client, "in-test", "done")
