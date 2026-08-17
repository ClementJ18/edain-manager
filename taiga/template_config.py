import os

DEBUG = False

# OAuth2 must make use of HTTPS in production environment.
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = (
    "true"  # !! Only in development environment.
)

TAIGA_URL_SECRET = ""
TAIGA_WEBHOOK = ""

BASE_URL = ""
USERNAME = ""
PASSWORD = ""
PROJECT_ID = 0000000

# The bot service account; its own webhook events are ignored to avoid loops.
TAIGA_BOT_USER_ID = 0

BETA_ROLE = ""
TEAM_ROLE = ""
GUILD_ID = ""

CLIENT_ID = ""
CLIENT_SECRET = ""
CLIENT_CALLBACK = ""

APP_SECRET = b""

STATUS_MAPPING = {
    "xxxxx": 000000,
}

EPIC_STATUS_MAPPING = {
    "xxxxxx": 0000000,
}
