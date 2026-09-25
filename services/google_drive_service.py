import io
import os
import logging

from dotenv import load_dotenv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from models import db, GoogleConnection
from services.google_oauth_service import (
    create_oauth_flow as _shared_create_oauth_flow,
    get_google_user_info,
)


load_dotenv()

log = logging.getLogger(__name__)


# ============================================================
# GOOGLE CONFIGURATION
# ============================================================

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/drive.readonly",
]

TOKEN_URI = "https://oauth2.googleapis.com/token"


# ============================================================
# OAUTH FLOW (PKCE)
# ============================================================

def create_oauth_flow(redirect_uri, code_verifier=None, state=None):
    """
    Create a Google OAuth 2.0 flow (Drive scopes) using PKCE.

    IMPORTANT: the code_verifier generated during /google/login
    must be persisted (server-side session) and reused unchanged
    during /google/callback, or the token exchange will fail.
    """
    return _shared_create_oauth_flow(
        redirect_uri, SCOPES, code_verifier=code_verifier, state=state
    )


# ============================================================
# USER CREDENTIALS
# ============================================================

def credentials_from_connection(connection):

    if not connection:
        return None

    return Credentials(
        token=connection.access_token,
        refresh_token=connection.refresh_token,
        token_uri=connection.token_uri or TOKEN_URI,
        client_id=connection.client_id,
        client_secret=connection.client_secret,
        scopes=(
            connection.scopes.split()
            if connection.scopes
            else SCOPES
        ),
    )


# ============================================================
# SAVE / REFRESH TOKEN
# ============================================================

def update_connection_credentials(connection, credentials):

    if credentials.token:
        connection.access_token = credentials.token

    if credentials.refresh_token:
        connection.refresh_token = credentials.refresh_token

    if credentials.expiry:
        connection.expires_at = credentials.expiry

    db.session.commit()


def save_connection_from_credentials(user, credentials, google_email, google_user_id=None):
    """
    Create or update the GoogleConnection row for this user from a
    freshly-obtained Credentials object. This is the single source of
    truth for stored Google tokens - nothing is stored on the User model.
    """

    connection = GoogleConnection.query.filter_by(user_id=user.id).first()

    if not connection:
        connection = GoogleConnection(user_id=user.id)

    connection.google_user_id = google_user_id
    connection.google_email = google_email
    connection.access_token = credentials.token
    connection.refresh_token = (
        credentials.refresh_token or connection.refresh_token
    )
    connection.token_uri = credentials.token_uri or TOKEN_URI
    connection.client_id = credentials.client_id
    connection.client_secret = credentials.client_secret
    connection.scopes = " ".join(credentials.scopes or SCOPES)
    connection.expires_at = credentials.expiry

    db.session.add(connection)
    db.session.commit()

    return connection


# ============================================================
# GET USER DRIVE SERVICE
# ============================================================

def get_drive_service(user):

    if not user:
        raise RuntimeError("Application user is not logged in.")

    connection = GoogleConnection.query.filter_by(user_id=user.id).first()

    if not connection:
        raise RuntimeError("Google Drive is not connected.")

    credentials = credentials_from_connection(connection)

    if not credentials:
        raise RuntimeError("Invalid Google credentials.")

    if credentials.expired:

        if not credentials.refresh_token:
            raise RuntimeError(
                "Google access token expired and no refresh token "
                "is available. Please reconnect Google Drive."
            )

        credentials.refresh(Request())
        update_connection_credentials(connection, credentials)

    return build("drive", "v3", credentials=credentials, cache_discovery=False)


# ============================================================
# USERINFO
#
# get_google_user_info is imported from google_oauth_service above.
# ============================================================


# ============================================================
# LIST DRIVE FILES
# ============================================================

def list_drive_files(user, search=""):

    drive = get_drive_service(user)

    query = (
        "trashed = false "
        "and mimeType != 'application/vnd.google-apps.folder'"
    )

    if search:
        # Escape backslashes first, then quotes, so a literal backslash
        # in user input can't be used to break out of the quoted string.
        safe_search = search.replace("\\", "\\\\").replace("'", "\\'")
        query += f" and name contains '{safe_search}'"

    response = drive.files().list(
        q=query,
        pageSize=100,
        orderBy="modifiedTime desc",
        fields=(
            "files(id,name,mimeType,size,modifiedTime,webViewLink,"
            "iconLink,capabilities/canDownload)"
        ),
    ).execute()

    return response.get("files", [])


# ============================================================
# DOWNLOAD FILE
# ============================================================

MAX_DRIVE_DOWNLOAD_BYTES = int(
    os.getenv("DRIVE_MAX_DOWNLOAD_MB", "25")
) * 1024 * 1024


def download_file_bytes(user, file_id):

    drive = get_drive_service(user)

    metadata = drive.files().get(
        fileId=file_id,
        fields="id,name,mimeType,size,capabilities/canDownload",
    ).execute()

    mime_type = metadata.get("mimeType", "")

    size = metadata.get("size")
    if size and int(size) > MAX_DRIVE_DOWNLOAD_BYTES:
        raise RuntimeError(
            "This file exceeds the maximum allowed download size "
            f"({MAX_DRIVE_DOWNLOAD_BYTES // (1024 * 1024)} MB)."
        )

    # --------------------------------------------------------
    # Google Workspace files (need export, not raw download)
    # --------------------------------------------------------

    export_map = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.presentation": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
    }

    if mime_type in export_map:
        result = drive.files().export(
            fileId=file_id, mimeType=export_map[mime_type]
        ).execute()
        return metadata, result

    # --------------------------------------------------------
    # Normal files
    # --------------------------------------------------------

    if "capabilities" in metadata and not metadata["capabilities"].get(
        "canDownload", True
    ):
        raise RuntimeError(
            "Google Drive does not allow this file to be downloaded."
        )

    request = drive.files().get(fileId=file_id, alt="media")
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    return metadata, buffer.getvalue()


# ============================================================
# DELETE / DISCONNECT GOOGLE DRIVE
# ============================================================

def disconnect_google_drive(user):

    connection = GoogleConnection.query.filter_by(user_id=user.id).first()

    if connection:
        db.session.delete(connection)
        db.session.commit()
