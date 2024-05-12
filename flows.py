import git
from taiga.config import REPO_PATH
from taiga.utils import Client, status_mappings
from taiga.move_column import move_column


def build_flow(
    is_beta: bool, version: str, candidate: str = 0, branch: str = "origin/main"
):
    # checkout branch and pull
    repo = git.Repo(REPO_PATH)
    repo.git.checkout(branch)

    #rebuild strings

    #rebuild asset?

    #build .big files

    # package big files together

    # runn inno setup?

    # upload to storage


def taiga_flow(is_beta: bool, version: str, candidate: str = 0):
    client = Client()
    client.auth()

    # mark previous epic as old
    epics = client.list_epics()

    version_tag = "beta" if is_beta else "release"
    epic = next(
        epic
        for epic in epics
        if epic["status_extra_info"]["name"] == "Current"
        and version_tag in epic["subject"].lower()
    )

    client.update_epic(epic["id"], epic["version"], status="old")

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

    # send discord webhook


def generate_bug_list(client: Client, version):
    stories = client.list_stories(status=status_mappings["awaiting-release"])
    with open("report.txt", "w") as f:
        f.write(
            f"Bugs Fixed in Version {version}\n"
            + "\n".join([story["subject"] for story in stories])
        )
