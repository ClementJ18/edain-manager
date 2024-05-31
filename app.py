import functools
import itertools
import logging
import math
from operator import itemgetter
import threading

import requests
from boto3 import Session
from flask import Flask, Response, redirect, render_template, request, url_for
from flask_discord import DiscordOAuth2Session
from flask_discord.exceptions import RateLimited
from markdownify import markdownify as md

from flows import RELEASE_LOG_FILE, REPO, build_lock, run_flows
from forms import VersionCreatorForm
from taiga.config import (
    APP_SECRET,
    BETA_ROLE,
    BUCKET_NAME,
    BUCKET_REGION,
    CLIENT_CALLBACK,
    CLIENT_ID,
    CLIENT_SECRET,
    DEBUG,
    GUILD_ID,
    SPACE_URL_SECRET,
    SPACE_WEBHOOK,
    TAIGA_URL_SECRET,
    SPACES_KEY,
    SPACES_SECRET,
    TEAM_ROLE,
    TAIGA_WEBHOOK,
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
            if DEBUG:
                return view(*args, **kwargs)

            if not discord.authorized:
                return discord.create_session(
                    scope=["identify", "guilds", "guilds.members.read"],
                    prompt=False,
                    data={"next": request.endpoint},
                )

            try:
                member = discord.request(f"/users/@me/guilds/{GUILD_ID}/member")
            except RateLimited:
                return (
                    render_template(
                        "message.html",
                        message="Currently being ratelimited, please try again later",
                        status=429,
                    ),
                    429,
                )

            response = (
                render_template(
                    "message.html", message="You are not welcome here!", status=403
                ),
                403,
            )
            if not member.get("roles"):
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
    return render_template("message.html", message="Go away.", status=418), 418


@app.route(f"/webhook/{SPACE_URL_SECRET}", methods=["POST"])
def space_webhook_receiver():
    data = request.json
    commit = data["payload"]["commit"]

    grouped_files = itertools.groupby(commit['changes']['changes'], lambda x: x['changeType'])
    fields = []
    for key, files in grouped_files:
        fields.append({
            "name": f"{key.title()} Files",
            "value": ("\n".join([f"- `{file['new']['path']}`" for file in files]))[:1024]
        })

    data = {
        "content": None,
        "embeds": [
            {
                "title": "File List",
                "description": f"Commit [**{commit['commit']['id']}**](<https://edain-mod.jetbrains.space/p/main/repositories/edain-mod-files/revision/{commit['commit']['id']}>) pushed to main. Message: \n >>> {commit['commit']['message']}",
                "color": 5814783,
                "fields": fields,
            }
        ],
        "username": "Git Reporter",
        "avatar_url": "https://git-scm.com/images/logos/downloads/Git-Icon-1788C.png",
        "attachments": [],
    }

    response = requests.post(SPACE_WEBHOOK, json=data)
    return Response(status=response.status_code, response=response.text)


@app.route(f"/webhook/{TAIGA_URL_SECRET}", methods=["POST"])
def taiga_webhook_receiver():
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

    response = requests.post(TAIGA_WEBHOOK, json=data)
    return Response(status=response.status_code, response=response.text)


def release_creator(is_beta: bool):
    if build_lock.locked():
        try:
            with open(RELEASE_LOG_FILE, "r") as f:
                text = f.read()
        except FileNotFoundError:
            text = ""

        return (
            render_template(
                "message.html",
                message="Another release is currently being created, please try again later...",
                status=423,
                logs=text,
            ),
            423,
        )

    return _release_creator(is_beta)


def _release_creator(is_beta: bool):
    form = VersionCreatorForm()
    remote_refs = REPO.remote().refs

    branches = [branch.name for branch in remote_refs]
    form.branch_name.choices = branches
    form.branch_name.data = "origin/main"

    commit_dict = {
        branch: " - ".join(
            [
                x.strip()
                for x in itemgetter(2, 4)(REPO.git.log(branch, n=1).splitlines())
            ]
        )
        for branch in branches
    }

    if request.method == "POST" and form.validate():
        thread = threading.Thread(
            target=run_flows,
            args=(
                is_beta,
                form.version_number.data,
                form.candidate_number.data if is_beta else None,
                discord.fetch_user(),
                {"taiga": form.taiga_flow.data, "build": form.build_flow.data},
                form.branch_name.data,
                form.commit_sha.data,
            ),
        )
        thread.start()

        if is_beta:
            msg = f"{form.version_number.data} Beta {form.candidate_number.data} is being created. You will receive a notification when it is done."
        else:
            msg = f"{form.version_number.data} Release is being created. You will receive a notification when it is done."

        return render_template("message.html", message=msg, status=202), 202

    return render_template(
        "release_creator.html", is_beta=is_beta, form=form, commits=commit_dict
    )


def humanize_bytes(size_bytes: int):
    if size_bytes == 0:
        return "0B"

    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return "%s %s" % (s, size_name[i])


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

    version_tag = "beta" if is_beta else "release"
    response = client.list_objects(Bucket=BUCKET_NAME, Prefix=version_tag)

    try:
        name_list = [
            (
                release["Key"],
                release["LastModified"].strftime("%Y-%m-%d %H:%M"),
                humanize_bytes(release["Size"]),
            )
            for release in response["Contents"][1:]
        ]
    except KeyError:
        name_list = []

    releases = []
    files = []
    for name in name_list:
        if name[0].startswith(f"{version_tag}/"):
            files.append(name)
        else:
            releases.append(name)

    releases.reverse()
    return render_template(
        "release_downloader.html", is_beta=is_beta, releases=releases, files=files
    )


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


@app.route("/bugs")
@scope_locked(team_only=True)
def bug_list():
    with open("report.txt", "r") as f:
        text = f.read()

    return (
        render_template(
            "message.html",
            message="See the list of fixed bugs for the latest release here",
            status=200,
            logs=text,
        ),
        200,
    )


if __name__ == "__main__":
    app.run(debug=True, ssl_context="adhoc")
