import functools
import logging
import threading

import git
import requests
from boto3 import Session
from flask import Flask, Response, redirect, render_template, request, url_for
from flask_discord import DiscordOAuth2Session
from markdownify import markdownify as md

from flows import build_lock, run_flows
from forms import VersionCreatorForm
from taiga.config import (
    APP_SECRET,
    BETA_ROLE,
    BUCKET_NAME,
    BUCKET_REGION,
    CLIENT_CALLBACK,
    CLIENT_ID,
    CLIENT_SECRET,
    GUILD_ID,
    REPO_PATH,
    SECRET,
    SPACES_KEY,
    SPACES_SECRET,
    TEAM_ROLE,
    WEBHOOK,
)

app = Flask(__name__)

app.secret_key = APP_SECRET
app.config["DISCORD_CLIENT_ID"] = CLIENT_ID  # Discord client ID.
app.config["DISCORD_CLIENT_SECRET"] = CLIENT_SECRET  # Discord client secret.
app.config["DISCORD_REDIRECT_URI"] = CLIENT_CALLBACK  # Discord client ID.
app.url_map.strict_slashes = False

discord = DiscordOAuth2Session(app)


def scope_locked(team_only: bool):
    def requires_authorization(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            if not discord.authorized:
                return discord.create_session(
                    scope=["identify", "guilds", "guilds.members.read"],
                    prompt=False,
                    data={"next": request.endpoint},
                )

            member = discord.request(f"/users/@me/guilds/{GUILD_ID}/member")

            response = Response(response="You are not welcome here!", status=403)
            if not member.get("roles"):
                logging.info("User %s is not authorized", member["user"]["username"])
                return response

            is_team = TEAM_ROLE in member["roles"]
            is_beta = BETA_ROLE in member["roles"]

            logging.info(
                "Member %s is a team member: %s or a beta tester: %s",
                member["user"]["username"],
                is_team,
                is_beta,
            )

            if is_beta and team_only:
                raise response
            elif not is_team and not is_beta:
                raise response

            return view(*args, **kwargs)

        return wrapper

    return requires_authorization


@app.route("/callback")
def login():
    next_endpoint = request.args.get("next", None) or discord.callback().get("next")
    if next_endpoint:
        return redirect(url_for(next_endpoint))

    return redirect(url_for(beta_download))


@app.route("/")
def index():
    return Response("Go away.", status=418)


@app.route(f"/webhook/{SECRET}", methods=["POST"])
def webhook_receiver():
    data = request.json
    if data["action"] not in ["create", "change", "test"]:
        return Response(status=200, response="Skipped, incorrect action")

    if data["type"] not in ["userstory", "test"]:
        return Response(status=200, response="Skipped, incorrect type")

    if data["by"]["id"] == 650088:  # botto
        return Response(status=200, response="Skipped, bot action")

    description = "test"
    title = "test"
    fields = [{"name": "Author", "value": data["by"]["username"]}]
    thumbnail = data["by"]["photo"]

    if data["type"] == "userstory":
        if data["action"] == "create":
            title = "Bug Report Created"
            description = f"[{data['data']['subject']}]({data['data']['permalink']})"
            fields.extend([{"name": "Tags", "value": ", ".join(data["data"]["tags"])}])
        elif (
            data["action"] == "change"
            and data["change"]["comment"]
            and not data["change"]["delete_comment_date"]
            and not data["change"]["edit_comment_date"]
        ):
            title = "Comment Added"
            description = md(data["change"]["comment_html"])
            fields.extend(
                [
                    {
                        "name": "Ticket",
                        "value": f"[{data['data']['subject']}]({data['data']['permalink']})",
                    }
                ]
            )
        else:
            return Response(
                status=200, response="Skipped, incorrect action for userstory"
            )

    data = {
        "content": None,
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": 5814783,
                "fields": fields,
                "thumbnail": {"url": thumbnail},
            }
        ],
        "username": "Issue Tracker",
        "avatar_url": "https://imgur.com/mn40JxG.png",
        "attachments": [],
    }

    response = requests.post(WEBHOOK, json=data)
    return Response(status=response.status_code, response=response.text)


def release_creator(is_beta: bool):
    if build_lock.locked():
        return Response(
            response="Another release is currently being created, please try again later...",
            status=423,
        )

    return _release_creator(is_beta)


def _release_creator(is_beta: bool):
    form = VersionCreatorForm()
    repo = git.Repo(REPO_PATH)
    remote_refs = repo.remote().refs

    form.branch_name.choices = [branch.name for branch in remote_refs]
    form.branch_name.data = "origin/main"

    if request.method == "POST" and form.validate():
        if is_beta:
            thread = threading.Thread(
                target=run_flows,
                args=(
                    is_beta,
                    form.version_number.data,
                    form.candidate_number.data,
                    form.branch_name.data,
                    discord.fetch_user(),
                ),
            )
            thread.start()
            return Response(
                f"{form.version_number.data} Beta {form.candidate_number.data} is being created. You will receive a notification when it is done.",
                200,
            )
        else:
            thread = threading.Thread(
                target=run_flows,
                args=(
                    is_beta,
                    form.version_number.data,
                    None,
                    form.branch_name.data,
                    discord.fetch_user(),
                ),
            )
            thread.start()
            return Response(
                f"{form.version_number.data} Release is being created. You will receive a notification when it is done.",
                200,
            )

    return render_template("release_creator.html", is_beta=is_beta, form=form)


def list_downloads(is_beta: bool):
    session = Session()
    client = session.client(
        "s3",
        region_name=BUCKET_REGION,
        endpoint_url=f"https://{BUCKET_REGION}.digitaloceanspaces.com",
        aws_access_key_id=SPACES_KEY,
        aws_secret_access_key=SPACES_SECRET,
    )

    if download := request.args.get("download", None):
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": download},
            ExpiresIn=1800,
        )

        return redirect(url)

    response = client.list_objects(
        Bucket=BUCKET_NAME, Prefix="beta" if is_beta else "release"
    )

    try:
        name_list = [release["Key"] for release in response["Contents"][1:]]
    except KeyError:
        name_list = []

    return render_template("release_downloader.html", is_beta=is_beta, names=name_list)


@app.route("/beta", methods=["GET", "POST"])
@scope_locked(team_only=False)
def beta_create():
    return release_creator(is_beta=True)


@app.route("/beta/download")
@scope_locked(team_only=True)
def beta_download():
    return list_downloads(is_beta=True)


@app.route("/release", methods=["GET", "POST"])
@scope_locked(team_only=True)
def release_create():
    return release_creator(is_beta=False)


@app.route("/release/download")
@scope_locked(team_only=True)
def release_download():
    return list_downloads(is_beta=False)


if __name__ == "__main__":
    app.run(debug=True, ssl_context="adhoc")
