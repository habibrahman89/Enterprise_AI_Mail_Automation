import os
import base64
import logging
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from models import db
from services.attachment_service import save_bytes, extract_attachment_text
from services.google_oauth_service import create_oauth_flow as _shared_create_oauth_flow

log = logging.getLogger(__name__)

# Requested when a user connects a Gmail mail account (distinct from
# the Drive connection's scopes - a user may have neither, either, or
# both connected).
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

TOKEN_URI = "https://oauth2.googleapis.com/token"


def create_oauth_flow(redirect_uri, code_verifier=None, state=None):
    """Create a Google OAuth 2.0 flow (Gmail mail scopes) using PKCE."""
    return _shared_create_oauth_flow(
        redirect_uri, SCOPES, code_verifier=code_verifier, state=state
    )


class GmailService:
    """
    Wraps the Gmail API for one specific connected MailAccount row.
    Credentials are read from (and refreshed back into) that row -
    nothing is read from local files, so multiple users can each have
    their own independently-authenticated Gmail connection.
    """

    def __init__(self, account):
        self.account = account

        credentials = Credentials(
            token=account.access_token,
            refresh_token=account.refresh_token,
            token_uri=account.token_uri or TOKEN_URI,
            client_id=account.client_id,
            client_secret=account.client_secret,
            scopes=(account.scopes.split() if account.scopes else SCOPES),
        )

        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            account.access_token = credentials.token
            if credentials.expiry:
                account.expires_at = credentials.expiry
            db.session.commit()

        self.service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def headers(self, p):
        return {h['name'].lower(): h['value'] for h in p.get('headers', [])}

    def parts(self, p, msgid, atts):
        out = []
        for x in p.get('parts', []):
            m = x.get('mimeType', '')
            fn = x.get('filename', '')
            b = x.get('body', {})
            if fn and b.get('attachmentId'):
                d = self.service.users().messages().attachments().get(
                    userId='me', messageId=msgid, id=b['attachmentId']
                ).execute().get('data', '')
                raw = base64.urlsafe_b64decode(d + '=' * (-len(d) % 4))
                try:
                    path = save_bytes(msgid, fn, raw)
                    atts.append({
                        'id': b['attachmentId'], 'filename': fn, 'content_type': m,
                        'size_bytes': len(raw), 'storage_path': path,
                        'extracted_text': extract_attachment_text(path, m),
                    })
                except ValueError as exc:
                    log.warning('Skipping attachment on message %s: %s', msgid, exc)
            elif m == 'text/plain' and b.get('data'):
                raw = base64.urlsafe_b64decode(b['data'] + '=' * (-len(b['data']) % 4))
                out.append(raw.decode('utf-8', 'replace'))
            elif x.get('parts'):
                out.append(self.parts(x, msgid, atts))
        return '\n'.join(out)

    def one(self, i):
        msg = self.service.users().messages().get(userId='me', id=i, format='full').execute()
        p = msg.get('payload', {})
        h = self.headers(p)
        a = []
        body = self.parts(p, msg['id'], a)
        dt = (
            datetime.fromtimestamp(int(msg['internalDate']) / 1000, tz=timezone.utc).replace(tzinfo=None)
            if msg.get('internalDate') else None
        )
        return {
            'id': msg['id'], 'thread_id': msg.get('threadId'), 'sender': h.get('from', ''),
            'recipients': h.get('to', ''), 'cc': h.get('cc', ''), 'subject': h.get('subject', ''),
            'body': body, 'received_at': dt,
            'is_read': 'UNREAD' not in msg.get('labelIds', []),
            'folder': 'Inbox' if 'INBOX' in msg.get('labelIds', []) else 'Archived',
            'attachments': a,
        }

    def fetch_recent(self, limit=100, scope='inbox'):
        q = {
            'all': '-in:sent -in:drafts -in:spam -in:trash',
            'unread': 'in:inbox is:unread',
            'promotions': 'category:promotions',
            'primary': 'category:primary',
        }.get(scope, 'in:inbox')
        out = []
        tok = None
        while len(out) < limit:
            r = self.service.users().messages().list(
                userId='me', q=q, maxResults=min(100, limit - len(out)), pageToken=tok
            ).execute()
            for x in r.get('messages', []):
                out.append(self.one(x['id']))
                if len(out) >= limit:
                    break
            tok = r.get('nextPageToken')
            if not tok:
                break
        return out

    def send_email(self, recipient, subject, body, attachments=None):
        m = MIMEMultipart()
        m['To'] = recipient
        m['Subject'] = subject
        m.attach(MIMEText(body, 'plain', 'utf-8'))
        for a in attachments or []:
            if a.storage_path and os.path.exists(a.storage_path):
                p = MIMEBase('application', 'octet-stream')
                p.set_payload(open(a.storage_path, 'rb').read())
                encoders.encode_base64(p)
                p.add_header('Content-Disposition', f'attachment; filename="{a.filename}"')
                m.attach(p)
        raw = base64.urlsafe_b64encode(m.as_bytes()).decode()
        self.service.users().messages().send(userId='me', body={'raw': raw}).execute()
