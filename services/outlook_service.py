import os
import base64
import logging
from datetime import datetime, timedelta

import requests

from models import db
from services.attachment_service import save_bytes, extract_attachment_text
from services.outlook_oauth_service import refresh_access_token

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"


class OutlookService:
    """
    Wraps Microsoft Graph for one specific connected MailAccount row.
    Tokens are read from (and refreshed back into) that row.
    """

    def __init__(self, account):
        self.account = account

        if not account.access_token or (
            account.expires_at and account.expires_at <= datetime.utcnow()
        ):
            if not account.refresh_token:
                raise RuntimeError(
                    "Outlook access token expired and no refresh token is "
                    "available. Please reconnect this Outlook account."
                )
            result = refresh_access_token(account.refresh_token)
            account.access_token = result["access_token"]
            account.refresh_token = result.get("refresh_token", account.refresh_token)
            expires_in = result.get("expires_in", 3600)
            account.expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            db.session.commit()

        self.token = account.access_token

    def h(self):
        return {'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'}

    def fetch_recent(self, limit=100, scope='inbox'):
        params = {'$top': str(min(limit, 100)), '$orderby': 'receivedDateTime desc', '$expand': 'attachments'}
        if scope == 'unread':
            params['$filter'] = 'isRead eq false'
        r = requests.get(f'{GRAPH}/me/mailFolders/inbox/messages', headers=self.h(), params=params, timeout=60)
        d = r.json()
        if 'error' in d:
            raise RuntimeError(d['error'].get('message', 'Graph error'))
        out = []
        for m in d.get('value', [])[:limit]:
            dt = datetime.fromisoformat(m['receivedDateTime'].replace('Z', '+00:00')).replace(tzinfo=None)
            aa = []
            for a in m.get('attachments', []):
                if a.get('@odata.type') != '#microsoft.graph.fileAttachment':
                    continue
                raw = base64.b64decode(a.get('contentBytes', '')) if a.get('contentBytes') else b''
                path = ''
                if raw:
                    try:
                        path = save_bytes(m['id'], a.get('name', 'attachment'), raw)
                    except ValueError as exc:
                        log.warning('Skipping attachment on message %s: %s', m['id'], exc)
                        continue
                aa.append({
                    'id': a.get('id'), 'filename': a.get('name'), 'content_type': a.get('contentType'),
                    'size_bytes': len(raw), 'storage_path': path,
                    'extracted_text': extract_attachment_text(path, a.get('contentType')) if path else '',
                })
            out.append({
                'id': m['id'], 'conversation_id': m.get('conversationId'),
                'sender': m.get('from', {}).get('emailAddress', {}).get('address', ''),
                'recipients': ', '.join(x.get('emailAddress', {}).get('address', '') for x in m.get('toRecipients', [])),
                'cc': ', '.join(x.get('emailAddress', {}).get('address', '') for x in m.get('ccRecipients', [])),
                'subject': m.get('subject', ''), 'body': m.get('body', {}).get('content', ''),
                'received_at': dt, 'is_read': m.get('isRead', False), 'folder': 'Inbox', 'attachments': aa,
            })
        return out

    def send_email(self, recipient, subject, body, attachments=None):
        aa = []
        for a in attachments or []:
            if a.storage_path and os.path.exists(a.storage_path):
                aa.append({
                    '@odata.type': '#microsoft.graph.fileAttachment', 'name': a.filename,
                    'contentType': a.content_type,
                    'contentBytes': base64.b64encode(open(a.storage_path, 'rb').read()).decode(),
                })
        p = {
            'message': {
                'subject': subject, 'body': {'contentType': 'Text', 'content': body},
                'toRecipients': [{'emailAddress': {'address': recipient}}], 'attachments': aa,
            },
            'saveToSentItems': True,
        }
        r = requests.post(f'{GRAPH}/me/sendMail', headers=self.h(), json=p, timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f'Graph sendMail failed: {r.status_code} {r.text}')
