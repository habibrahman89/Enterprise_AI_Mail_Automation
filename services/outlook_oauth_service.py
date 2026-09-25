import os
import logging

import msal
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

CLIENT_ID = os.getenv("OUTLOOK_CLIENT_ID")
TENANT_ID = os.getenv("OUTLOOK_TENANT_ID", "common")

SCOPES = ["Mail.ReadWrite", "Mail.Send", "User.Read", "offline_access"]


def _app():
    if not CLIENT_ID:
        raise RuntimeError("OUTLOOK_CLIENT_ID is not configured.")
    return msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    )


def initiate_auth_code_flow(redirect_uri):
    """
    Starts an MSAL authorization-code + PKCE flow for the web.
    The returned dict must be stashed in the user's session and passed
    back into complete_auth_code_flow() unchanged.
    """
    return _app().initiate_auth_code_flow(
        SCOPES,
        redirect_uri=redirect_uri,
    )


def complete_auth_code_flow(flow, auth_response):
    """
    Exchanges the authorization response (request.args as a dict) for
    tokens using the flow state saved by initiate_auth_code_flow().
    """
    result = _app().acquire_token_by_auth_code_flow(flow, auth_response)

    if "access_token" not in result:
        raise RuntimeError(
            result.get("error_description", "Outlook authentication failed.")
        )

    return result


def refresh_access_token(refresh_token):
    """
    Exchanges a stored refresh_token for a new access_token, used when
    a connected account's access token has expired.
    """
    result = _app().acquire_token_by_refresh_token(refresh_token, scopes=SCOPES)

    if "access_token" not in result:
        raise RuntimeError(
            result.get("error_description", "Outlook token refresh failed.")
        )

    return result
