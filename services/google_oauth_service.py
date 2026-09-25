import os
import logging

from dotenv import load_dotenv

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

load_dotenv()

log = logging.getLogger(__name__)

CLIENT_SECRETS_FILE = os.getenv(
    "GOOGLE_CLIENT_SECRETS_FILE",
    "google_client_secret.json"
)

TOKEN_URI = "https://oauth2.googleapis.com/token"


def create_oauth_flow(redirect_uri, scopes, code_verifier=None, state=None):
    """
    Build a Google OAuth 2.0 flow using PKCE for the given scopes.
    Shared by the Google Drive connection and the Gmail mail-account
    connection - each requests a different scope set but goes through
    the same PKCE mechanics.
    """

    if not os.path.exists(CLIENT_SECRETS_FILE):
        raise FileNotFoundError(
            f"Google OAuth credentials file not found: {CLIENT_SECRETS_FILE}"
        )

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=scopes,
        state=state,
        autogenerate_code_verifier=(code_verifier is None),
    )

    if code_verifier:
        flow.code_verifier = code_verifier

    flow.redirect_uri = redirect_uri

    return flow


def get_google_user_info(credentials):
    service = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
    return service.userinfo().get().execute()
