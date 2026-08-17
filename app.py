import functools
import logging
import math
import threading

import requests
from flask import (
    Flask,
    Response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_discord import DiscordOAuth2Session
from flask_discord.exceptions import RateLimited
from markdownify import markdownify as md
from werkzeug.exceptions import RequestEntityTooLarge

import patching
from flows import BUG_REPORT_FILE, RELEASE_LOG_FILE, flow_lock, run_flows
from forms import PatcherForm, VersionCreatorForm
from taiga.config import (
    APP_SECRET,
    BETA_ROLE,
    CLIENT_CALLBACK,
    CLIENT_ID,
    CLIENT_SECRET,
    DEBUG,
    GUILD_ID,
    TAIGA_BOT_USER_ID,
    TAIGA_URL_SECRET,
    TEAM_ROLE,
    TAIGA_WEBHOOK,
)
from taiga.utils import REQUEST_TIMEOUT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)

# Discord rejects the whole webhook payload if an embed exceeds these limits.
DISCORD_DESCRIPTION_LIMIT = 4096

app = Flask(__name__)

app.secret_key = APP_SECRET
app.config["DISCORD_CLIENT_ID"] = CLIENT_ID  # Discord client ID.
app.config["DISCORD_CLIENT_SECRET"] = CLIENT_SECRET  # Discord client secret.
app.config["DISCORD_REDIRECT_URI"] = CLIENT_CALLBACK  # Discord client ID.
app.config["MAX_CONTENT_LENGTH"] = patching.MAX_UPLOAD_BYTES
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

            def forbidden():
                return (
                    render_template(
                        "message.html", message="You are not welcome here!", status=403
                    ),
                    403,
                )

            roles = member.get("roles")
            if not roles:
                return forbidden()

            is_team = TEAM_ROLE in roles
            is_beta = BETA_ROLE in roles

            logging.info(
                "Member %s is a team member: %s or a beta tester: %s",
                member["user"]["username"],
                is_team,
                is_beta,
            )

            if not is_team and (team_only or not is_beta):
                return forbidden()

            return view(*args, **kwargs)

        return wrapper

    return requires_authorization


@app.route("/callback")
def login():
    # discord.callback() completes the OAuth token exchange, so it must always run
    # before we inspect where to send the user next.
    next_endpoint = discord.callback().get("next")
    if next_endpoint and next_endpoint in app.view_functions:
        return redirect(url_for(next_endpoint))

    return redirect(url_for("bug_list"))


@app.route("/")
def index():
    return render_template("message.html", message="Go away.", status=418), 418


@app.route(f"/webhook/{TAIGA_URL_SECRET}", methods=["POST"])
def taiga_webhook_receiver():
    data = request.get_json(silent=True)
    if not data:
        return Response(status=400, response="Expected a JSON body")

    if data["action"] not in ["create", "change", "test"]:
        return Response(status=200, response="Skipped, incorrect action")

    if data["type"] not in ["userstory", "test"]:
        return Response(status=200, response="Skipped, incorrect type")

    if data["by"]["id"] == TAIGA_BOT_USER_ID:
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
            description = md(data["change"]["comment_html"])[:DISCORD_DESCRIPTION_LIMIT]
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

    embed = {
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

    response = requests.post(TAIGA_WEBHOOK, json=embed, timeout=REQUEST_TIMEOUT)
    return Response(status=response.status_code, response=response.text)


def release_creator(is_beta: bool):
    if flow_lock.locked():
        try:
            with open(RELEASE_LOG_FILE, "r") as f:
                text = f.read()
        except FileNotFoundError:
            text = ""

        return (
            render_template(
                "message.html",
                message="Another flow is currently running, please try again later...",
                status=423,
                logs=text,
            ),
            423,
        )

    return _release_creator(is_beta)


def _release_creator(is_beta: bool):
    form: VersionCreatorForm = VersionCreatorForm()

    if request.method == "POST" and form.validate():
        # Off the request thread: moving a column is one PATCH per ticket, which can
        # outlast gunicorn's 30s worker timeout on a busy board.
        thread = threading.Thread(
            target=run_flows,
            args=(
                is_beta,
                form.version_number.data,
                form.candidate_number.data if is_beta else None,
                discord.fetch_user(),
            ),
        )
        thread.start()

        if is_beta:
            msg = f"The board is being prepared for {form.version_number.data} Beta {form.candidate_number.data}. You will receive a notification when it is done."
        else:
            msg = f"The board is being prepared for {form.version_number.data} Release. You will receive a notification when it is done."

        return render_template("message.html", message=msg, status=202), 202

    return render_template("release_creator.html", is_beta=is_beta, form=form)


def humanize_bytes(size_bytes: int):
    if size_bytes == 0:
        return "0B"

    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = min(int(math.floor(math.log(size_bytes, 1024))), len(size_name) - 1)
    size = round(size_bytes / (1024**i), 2)
    return f"{size} {size_name[i]}"


@app.route("/beta", methods=["GET", "POST"])
@scope_locked(team_only=True)
def beta_create():
    return release_creator(is_beta=True)


@app.route("/release", methods=["GET", "POST"])
@scope_locked(team_only=True)
def release_create():
    return release_creator(is_beta=False)


@app.route("/bugs")
@scope_locked(team_only=True)
def bug_list():
    try:
        with open(BUG_REPORT_FILE, "r") as f:
            text = f.read()
    except FileNotFoundError:
        # Written by the taiga flow of a release; absent until one has run.
        text = "No bug report has been generated yet."

    return (
        render_template(
            "message.html",
            message="See the list of fixed bugs for the latest release here",
            status=200,
            logs=text,
        ),
        200,
    )


@app.route("/patch", methods=["GET", "POST"])
def patch_engine():
    """Apply pySAGE's binary patches to an uploaded game.dat.

    Open to anyone: the patches are engine-level and benefit every mod on ROTWK, so this is not a
    team tool. What is asked in return is attribution, which is why the form will not submit
    without the credit agreement and the page names an author per patch.
    """
    if not patching.AVAILABLE:
        return (
            render_template(
                "message.html",
                message="Patching is unavailable: pysage-tools is not installed on this server.",
                status=503,
            ),
            503,
        )

    patching.sweep()
    form = PatcherForm()
    error = None

    if form.validate_on_submit():
        try:
            chosen = patching.selections(request.form)
            if not form.experimental_agreement.data and any(
                patch.experimental
                for selection in chosen
                for patch in selection.patches
            ):
                raise patching.PatchError(
                    "You picked an experimental patch, so the acknowledgement under "
                    "'Experimental patches' has to be ticked as well."
                )

            result = patching.apply_selected(chosen, request.files)

            for patched in result.files:
                logging.info(
                    "Patched %s with %d patch(es): %s",
                    patched.filename,
                    len(patched.credits),
                    ", ".join(patched.credits),
                )

            return render_template(
                "patch_result.html",
                result=result,
                humanize=humanize_bytes,
                experimental_warning=patching.EXPERIMENTAL_WARNING,
                ttl_minutes=patching.OUTPUT_TTL // 60,
            )
        except patching.PatchError as exc:
            error = str(exc)

    return render_template(
        "patcher.html",
        form=form,
        targets=patching.TARGETS,
        experimental_warning=patching.EXPERIMENTAL_WARNING,
        ttl_minutes=patching.OUTPUT_TTL // 60,
        # Keep the selection on a rejected submission: rebuilding it from request.form beats
        # making somebody tick thirty boxes again over one bad parameter. The uploads cannot be
        # kept the same way - a browser will not let a file input be re-filled - so they have to
        # be picked again.
        resubmit=request.method == "POST",
        error=error,
    )


def serve_patched(token: str, slug: str, sagepatch: bool):
    output = patching.output_for(token, slug, sagepatch)
    if output is None:
        return (
            render_template(
                "message.html",
                message="That patched file has expired. Patched files are kept for "
                f"{patching.OUTPUT_TTL // 60} minutes; run the patch again.",
                status=404,
            ),
            404,
        )

    return send_file(
        output,
        as_attachment=True,
        download_name=patching.download_name(output, slug, sagepatch),
    )


@app.route("/patch/download/<token>/<slug>")
def patch_download(token: str, slug: str):
    return serve_patched(token, slug, sagepatch=False)


# A separate endpoint rather than one with an optional segment: two rules that differ only by a
# default make url_for ambiguous, and it resolves in favour of the longer one - so every download
# link on the result page came out pointing at the .sagepatch.
@app.route("/patch/sagepatch/<token>/<slug>")
def patch_sagepatch(token: str, slug: str):
    return serve_patched(token, slug, sagepatch=True)


@app.errorhandler(RequestEntityTooLarge)
def too_large(error):
    return (
        render_template(
            "message.html",
            message="That upload is too large. The limit is "
            f"{humanize_bytes(patching.MAX_UPLOAD_BYTES)} for everything sent at once, so a "
            "submission picking patches for several binaries has to fit in that together.",
            status=413,
        ),
        413,
    )


if __name__ == "__main__":
    app.run(debug=True, ssl_context="adhoc")
