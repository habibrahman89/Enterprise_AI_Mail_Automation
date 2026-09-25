import logging

from flask import request
from models import db, AuditLog

log = logging.getLogger(__name__)


def audit(action, details='', user_id=None, email_id=None):
    try:
        db.session.add(AuditLog(
            action=action,
            details=details,
            user_id=user_id,
            email_id=email_id,
            ip_address=request.remote_addr if request else '',
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Failed to write audit log entry action=%s", action)
