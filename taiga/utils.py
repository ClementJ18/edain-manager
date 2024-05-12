import requests

from config import BASE_URL, USERNAME, PASSWORD, PROJECT_ID

status_mappings = {
    "to-do": 9301941,  # todo
    "in-progress": 9301944,  # in progress
    "fixed-internally": 9301945,  # fixed internally
    "in-test": 9301946,  # in test
    "awaiting-release": 9301947,  # awaiting release
    "done": 9301948,  # done
}


class Client:
    def __init__(
        self,
        base_url: str = BASE_URL,
        username: str = USERNAME,
        password: str = PASSWORD,
        project_id: int = PROJECT_ID,
    ):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.project_id = project_id

        self.header = None
        self.session = requests.Session()
        self.session.hooks = {
            "response": lambda r, *args, **kwargs: r.raise_for_status()
        }

    def auth(self) -> dict:
        response = self.session.post(
            self.base_url + "/auth",
            data={
                "password": self.password,
                "username": self.username,
                "type": "normal",
            },
        )

        self.header = {
            "Authorization": f"Bearer {response.json()['auth_token']}",
            "x-disable-pagination": "True",
        }
        return response.json()

    def get_issue_history(self, issue_id: int):
        response = self.session.get(
            self.base_url + f"/history/userstory/{issue_id}",
            headers=self.header,
        )

        return response.json()

    def list_stories(self, *, status: int = None, tags: list = None) -> list:
        params = {
            "project": self.project_id,
        }

        if status is not None:
            params["status"] = status

        if tags is not None:
            params["tags"] = ",".join(tags)

        response = self.session.get(
            self.base_url + "/userstories", headers=self.header, params=params
        )

        return response.json()

    def list_epics(self):
        response = self.session.get(
            self.base_url + "/epics",
            headers=self.header,
            params={
                "project": self.project_id,
            },
        )

        return response.json()

    def attach_issue_to_epic(self, epic_id: int, issue_id: int):
        self.session.post(
            self.base_url + f"/epics/{epic_id}/related_userstories",
            headers=self.header,
            data={"epic": epic_id, "user_story": issue_id},
        )

    def bulk_order_stories(self, issues: list, status: int):
        self.session.post(
            self.base_url + "/userstories/bulk_update_kanban_order",
            headers=self.header,
            json={
                "bulk_userstories": issues,
                "project_id": self.project_id,
                "status_id": status,
            },
        )

    def update_story(
        self,
        us_id: int,
        version: int,
        *,
        status: int = None,
        tags: list = None,
        comment: str = None,
    ):
        data = {"version": version}

        if status is not None:
            data["status"] = status

        if tags is not None:
            data["tags"] = [tag[0] for tag in tags]

        if comment is not None:
            data["comment"] = comment

        self.session.patch(
            self.base_url + f"/userstories/{us_id}", headers=self.header, json=data
        )

    def update_epic(self, epic_id: int, version: int, *, status: str = None):
        data = {"version": version}

        if status is not None:
            data["status"] = status

        self.session.patch(
            self.base_url + f"/userstories/{epic_id}", headers=self.header, json=data
        )

    def get_story_attributes(self, story_id: int) -> dict:
        response = self.session.get(
            self.base_url + f"/userstories/custom-attributes-values/{story_id}",
            headers=self.header,
        )

        return response.json()

    def create_tag(self, name: str, color: str):
        response = self.session.post(
            self.base_url + f"/projects/{self.project_id}/create_tag",
            headers=self.header,
            data={"color": color, "tag": name},
        )

        return response.json()

    def create_epic(self, name: str):
        response = self.session.post(
            self.base_url + f"/epics",
            headers=self.header,
            data={"project": self.project_id, "subject": name},
        )

        return response.json()
