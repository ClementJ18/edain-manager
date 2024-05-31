import os

DEBUG = False

# OAuth2 must make use of HTTPS in production environment.
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = (
    "true"  # !! Only in development environment.
)

TAIGA_URL_SECRET = ""
TAIGA_WEBHOOK = ""
SPACE_URL_SECRET = ""
SPACE_WEBHOOK = ""

BASE_URL = ""
USERNAME = ""
PASSWORD = ""
PROJECT_ID = 0000000

BETA_ROLE = ""
TEAM_ROLE = ""
GUILD_ID = ""

REPO_PATH = ""
CLIENT_ID = ""
CLIENT_SECRET = ""
CLIENT_CALLBACK = ""

APP_SECRET = b""

SPACES_KEY = ""
SPACES_SECRET = ""
BUCKET_REGION = ""
BUCKET_NAME = ""
BUCKET_ENDPOINT = ""

REMOTE_URL = ""
REMOTE_PASS = ""

STATUS_MAPPING = {
    "xxxxx": 000000,
}

EPIC_STATUS_MAPPING = {
    "xxxxxx": 0000000,
}
