import functools
import logging
from flask import Flask, redirect, render_template, request, Response, url_for
import requests
from markdownify import markdownify as md
from flask_discord import DiscordOAuth2Session, Unauthorized
import git

from flows import build_flow, taiga_flow
from forms import VersionCreatorForm
from taiga.config import APP_SECRET, CLIENT_ID, CLIENT_SECRET, REPO_PATH, SECRET, WEBHOOK, GUILD_ID, BETA_ROLE, TEAM_ROLE

app = Flask(__name__)

app.secret_key = APP_SECRET
app.config["DISCORD_CLIENT_ID"] = CLIENT_ID  # Discord client ID.
app.config["DISCORD_CLIENT_SECRET"] = CLIENT_SECRET  # Discord client secret.

app.config["DISCORD_REDIRECT_URI"] = ""  # URL to your callback endpoint.
app.config["DISCORD_BOT_TOKEN"] = ""
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

            if not member.get("roles"):
                user = discord.fetch_user()
                logging.info("User %s is not authorized", user.username)
                raise Unauthorized

            is_team = TEAM_ROLE in member["roles"]
            is_beta = BETA_ROLE in member["roles"]

            logging.info(
                "Member %s is a team member: %s or a beta tester: %s",
                member["user"]["username"],
                is_team,
                is_beta,
            )

            if is_beta and team_only:
                raise Unauthorized
            elif not is_team and not is_beta:
                raise Unauthorized

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


def release_creator(release: bool):
    form = VersionCreatorForm()
    repo = git.Repo(REPO_PATH)
    remote_refs = repo.remote().refs

    form.branch_name.choices = [branch.name for branch in remote_refs]
    form.branch_name.data = "origin/main"
    print(form.data)

    if request.method == "POST" and form.validate():
        build_flow(
            is_beta=True,
            version=form.version_number.data,
            candidate=form.candidate_number.data,
            branch=form.branch_name.data,
        )
        taiga_flow(
            is_beta=True,
            version=form.version_number.data,
            candidate=form.candidate_number.data,
        )
        if not release:
            return Response(
                f"{form.version_number.data} Beta {form.candidate_number.data} created!",
                200,
            )
        else:
            return Response(f"{form.version_number.data} Release created!", 200)

    return render_template("release_creator.html", release=release, form=form)


@app.route("/beta", methods=["GET", "POST"])
@scope_locked(team_only=False)
def beta_create():
    return release_creator(release=False)


@app.route("/beta/download")
@scope_locked(team_only=True)
def beta_download():
    return "Beta Download Page"


@app.route("/release", methods=["GET", "POST"])
@scope_locked(team_only=True)
def release_create():
    return release_creator(release=True)


@app.route("/release/download")
@scope_locked(team_only=True)
def release_download():
    return Response("Release Download Page", 200)


if __name__ == "__main__":
    app.run(debug=True, ssl_context="adhoc")
