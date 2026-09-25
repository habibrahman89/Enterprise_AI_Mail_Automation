import json
import os

from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash


db = SQLAlchemy()


# ============================================================
# USER
# ============================================================

class User(db.Model):

    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(
        db.String(120),
        unique=True,
        nullable=False
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False
    )

    role = db.Column(
        db.String(30),
        default="ADMIN"
    )

    is_admin = db.Column(
        db.Boolean,
        default=False
    )

    email = db.Column(
        db.String(255)
    )

    active = db.Column(
        db.Boolean,
        default=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    google_connection = db.relationship(
        "GoogleConnection",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan"
    )

    mail_accounts = db.relationship(
        "MailAccount",
        backref="user",
        cascade="all, delete-orphan"
    )

    @staticmethod
    def ensure_admin():

        username = os.getenv(
            "ADMIN_USERNAME",
            "admin"
        )

        password = os.getenv(
            "ADMIN_PASSWORD",
            "ChangeMe123!"
        )

        user = User.query.filter_by(
            username=username
        ).first()

        if not user:

            db.session.add(
                User(
                    username=username,
                    password_hash=generate_password_hash(
                        password
                    ),
                    role="ADMIN",
                    is_admin=True
                )
            )

            db.session.commit()


# ============================================================
# MAIL ACCOUNT
#
# A single connected mailbox (Gmail or Outlook) belonging to one
# application user. Each user can connect their own mailbox(es);
# emails synced through an account are only ever visible to that
# account's owning user.
# ============================================================

class MailAccount(db.Model):

    __tablename__ = "mail_account"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True
    )

    provider = db.Column(
        db.String(20),
        nullable=False
    )  # 'gmail' or 'outlook'

    email_address = db.Column(
        db.String(500)
    )

    access_token = db.Column(
        db.Text
    )

    refresh_token = db.Column(
        db.Text
    )

    token_uri = db.Column(
        db.String(500)
    )

    client_id = db.Column(
        db.Text
    )

    client_secret = db.Column(
        db.Text
    )

    scopes = db.Column(
        db.Text
    )

    expires_at = db.Column(
        db.DateTime
    )

    active = db.Column(
        db.Boolean,
        default=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )


# ============================================================
# GOOGLE CONNECTION
# ============================================================

class GoogleConnection(db.Model):

    __tablename__ = "google_connection"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        unique=True,
        nullable=False
    )

    google_user_id = db.Column(
        db.String(255)
    )

    google_email = db.Column(
        db.String(500)
    )

    access_token = db.Column(
        db.Text,
        nullable=False
    )

    refresh_token = db.Column(
        db.Text
    )

    token_uri = db.Column(
        db.String(500),
        default="https://oauth2.googleapis.com/token"
    )

    client_id = db.Column(
        db.Text
    )

    client_secret = db.Column(
        db.Text
    )

    scopes = db.Column(
        db.Text
    )

    expires_at = db.Column(
        db.DateTime
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )


# ============================================================
# EMAIL
# ============================================================

class Email(db.Model):

    __table_args__ = (
        db.UniqueConstraint(
            "mail_account_id", "provider_message_id",
            name="uq_email_account_message"
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    mail_account_id = db.Column(
        db.Integer,
        db.ForeignKey("mail_account.id"),
        nullable=False,
        index=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True
    )

    provider = db.Column(
        db.String(30),
        nullable=False
    )

    provider_message_id = db.Column(
        db.String(500),
        nullable=False
    )

    thread_id = db.Column(
        db.String(500),
        index=True
    )

    conversation_id = db.Column(
        db.String(500),
        index=True
    )

    sender = db.Column(
        db.String(500),
        index=True
    )

    recipients = db.Column(
        db.Text,
        default=""
    )

    cc = db.Column(
        db.Text,
        default=""
    )

    subject = db.Column(
        db.Text,
        default=""
    )

    body = db.Column(
        db.Text,
        default=""
    )

    body_html = db.Column(
        db.Text,
        default=""
    )

    received_at = db.Column(
        db.DateTime,
        index=True
    )

    is_read = db.Column(
        db.Boolean,
        default=False
    )

    folder = db.Column(
        db.String(100),
        default="Inbox"
    )

    category = db.Column(
        db.String(100),
        default="Unclassified",
        index=True
    )

    priority = db.Column(
        db.String(20),
        default="MEDIUM",
        index=True
    )

    risk_level = db.Column(
        db.String(20),
        default="LOW"
    )

    summary = db.Column(
        db.Text,
        default=""
    )

    action_required = db.Column(
        db.Boolean,
        default=False,
        index=True
    )

    ai_processed = db.Column(
        db.Boolean,
        default=False,
        index=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    attachments = db.relationship(
        "Attachment",
        backref="email",
        cascade="all, delete-orphan"
    )

    analysis = db.relationship(
        "AIAnalysis",
        backref="email",
        uselist=False,
        cascade="all, delete-orphan"
    )

    drafts = db.relationship(
        "Draft",
        backref="email",
        cascade="all, delete-orphan"
    )

    tasks = db.relationship(
        "Task",
        backref="email",
        cascade="all, delete-orphan"
    )


# ============================================================
# ATTACHMENT
# ============================================================

class Attachment(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    email_id = db.Column(
        db.Integer,
        db.ForeignKey("email.id"),
        nullable=False
    )

    provider_attachment_id = db.Column(
        db.String(500)
    )

    filename = db.Column(
        db.String(500)
    )

    content_type = db.Column(
        db.String(255)
    )

    size_bytes = db.Column(
        db.Integer,
        default=0
    )

    storage_path = db.Column(
        db.String(1000)
    )

    extracted_text = db.Column(
        db.Text,
        default=""
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


# ============================================================
# AI ANALYSIS
# ============================================================

class AIAnalysis(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    email_id = db.Column(
        db.Integer,
        db.ForeignKey("email.id"),
        nullable=False,
        unique=True
    )

    category = db.Column(db.String(100))

    priority = db.Column(db.String(20))

    summary = db.Column(db.Text)

    action_required = db.Column(
        db.Boolean,
        default=False
    )

    action = db.Column(db.Text)

    deadline = db.Column(db.String(100))

    sentiment = db.Column(db.String(30))

    reply_required = db.Column(
        db.Boolean,
        default=False
    )

    confidence = db.Column(
        db.Float,
        default=0
    )

    entities = db.Column(
        db.Text,
        default="[]"
    )

    risk_level = db.Column(
        db.String(20),
        default="LOW"
    )

    recommended_route = db.Column(
        db.String(100)
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    def entity_list(self):

        try:
            return json.loads(
                self.entities or "[]"
            )

        except Exception:
            return []


# ============================================================
# DRAFT
# ============================================================

class Draft(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    email_id = db.Column(
        db.Integer,
        db.ForeignKey("email.id"),
        nullable=False
    )

    recipient = db.Column(
        db.String(1000)
    )

    subject = db.Column(
        db.Text
    )

    body = db.Column(
        db.Text
    )

    status = db.Column(
        db.String(20),
        default="DRAFT"
    )

    sent_at = db.Column(
        db.DateTime
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


# ============================================================
# TASK
# ============================================================

class Task(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    email_id = db.Column(
        db.Integer,
        db.ForeignKey("email.id")
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        index=True
    )

    title = db.Column(
        db.String(500),
        nullable=False
    )

    description = db.Column(
        db.Text,
        default=""
    )

    priority = db.Column(
        db.String(20),
        default="MEDIUM"
    )

    due_date = db.Column(
        db.DateTime
    )

    status = db.Column(
        db.String(20),
        default="OPEN"
    )

    assigned_to = db.Column(
        db.String(255),
        default=""
    )

    completed_at = db.Column(
        db.DateTime
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    @staticmethod
    def parse_due_date(value):

        if not value:
            return None

        try:

            from dateutil import parser

            return parser.parse(
                value,
                fuzzy=True
            ).replace(tzinfo=None)

        except Exception:

            return None


# ============================================================
# VENDOR
# ============================================================

class Vendor(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(255),
        nullable=False
    )

    email = db.Column(
        db.String(500)
    )

    domain = db.Column(
        db.String(255),
        index=True
    )

    phone = db.Column(
        db.String(100)
    )

    notes = db.Column(
        db.Text
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


# ============================================================
# AUTOMATION RULE
# ============================================================

class AutomationRule(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(255),
        nullable=False
    )

    condition_json = db.Column(
        db.Text,
        default="{}"
    )

    action_json = db.Column(
        db.Text,
        default="{}"
    )

    enabled = db.Column(
        db.Boolean,
        default=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


# ============================================================
# AUDIT LOG
# ============================================================

class AuditLog(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.Integer
    )

    email_id = db.Column(
        db.Integer
    )

    action = db.Column(
        db.String(100),
        nullable=False
    )

    details = db.Column(
        db.Text
    )

    ip_address = db.Column(
        db.String(100)
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )