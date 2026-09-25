import os
import json
import logging
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify, abort
)
from sqlalchemy import or_
from werkzeug.security import generate_password_hash
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from models import (
    db, User, Email, AIAnalysis, Draft, Task, Vendor,
    AuditLog, Attachment, GoogleConnection, MailAccount
)
from services.auth_service import verify_password
from services.mail_service import get_mail_service
from services.ai_service import analyze_email, generate_reply, summarize_text
from services.automation_service import process_rules
from services.audit_service import audit
from services.document_service import extract_text
from services.google_drive_service import (
    create_oauth_flow,
    save_connection_from_credentials,
    list_drive_files,
    download_file_bytes,
    disconnect_google_drive,
)
from services.gmail_service import create_oauth_flow as create_gmail_oauth_flow
from services.google_oauth_service import get_google_user_info
from services.outlook_oauth_service import (
    initiate_auth_code_flow as initiate_outlook_flow,
    complete_auth_code_flow as complete_outlook_flow,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("app")

# ============================================================
# APP / CONFIG
# ============================================================

app = Flask(__name__)

DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")

# Refuse to boot with placeholder secrets outside of local debug mode -
# this is the single easiest way an internal tool ends up compromised.
if not DEBUG and (
    SECRET_KEY in ("change-this-secret", "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET")
    or ADMIN_PASSWORD in ("ChangeMe123!", "CHANGE_THIS_STRONG_PASSWORD")
):
    raise RuntimeError(
        "Refusing to start: SECRET_KEY and/or ADMIN_PASSWORD are still set "
        "to their placeholder values in .env. Set real values, or set "
        "FLASK_DEBUG=1 for local development only."
    )

app.config["SECRET_KEY"] = SECRET_KEY
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL", "sqlite:///enterprise_mail_ai.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Session / cookie hardening
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 8  # 8 hours

# Reject oversized request bodies outright (attachments, form posts).
app.config["MAX_CONTENT_LENGTH"] = int(
    os.getenv("ATTACHMENT_MAX_SIZE_MB", "25")
) * 1024 * 1024 + (1 * 1024 * 1024)  # + 1MB slack for form overhead

db.init_app(app)

# Only relax OAuth's HTTPS requirement when explicitly opted in for local dev.
if os.getenv("OAUTH_INSECURE_TRANSPORT", "0") == "1":
    if not DEBUG:
        log.warning(
            "OAUTH_INSECURE_TRANSPORT=1 while FLASK_DEBUG=0 - this should "
            "only ever be enabled for local development."
        )
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

csrf = CSRFProtect(app)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

with app.app_context():
    db.create_all()
    User.ensure_admin()

scheduler = None
if os.getenv("SCHEDULER_ENABLED", "0") == "1":
    from services.scheduler_service import start_scheduler
    scheduler = start_scheduler(app)


# ============================================================
# SECURITY HEADERS
# ============================================================

@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # Local admin tool, single origin, but templates load Bootstrap /
    # icons / fonts from CDNs, so those specific origins are allowlisted
    # rather than using an open 'unsafe' policy.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: https:; "
        "script-src 'self' https://cdn.jsdelivr.net"
    )
    if app.config["SESSION_COOKIE_SECURE"]:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


# ============================================================
# AUTH HELPERS
# ============================================================

def login_required(f):
    @wraps(f)
    def wrapped(*a, **k):
        if session.get("user_id"):
            return f(*a, **k)
        return redirect(url_for("login"))
    return wrapped


def admin_required(f):
    @wraps(f)
    def wrapped(*a, **k):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if not user.is_admin:
            abort(403)
        return f(*a, **k)
    return wrapped


def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


def owned_email_or_404(id):
    """
    Fetch an Email by id, but only if it belongs to the currently
    logged-in user - each person only ever sees mail from mailboxes
    they personally connected.
    """
    e = Email.query.get_or_404(id)
    if e.user_id != session.get("user_id"):
        abort(404)
    return e


@app.context_processor
def inject_globals():
    return {
        "user": current_user(),
        "current_user": current_user(),
    }


# ============================================================
# AUTH ROUTES
# ============================================================

@app.route("/login", methods=["GET", "POST"])
@limiter.limit(lambda: os.getenv("LOGIN_RATE_LIMIT", "10/minute"))
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username, active=True).first()

        if user and verify_password(password, user.password_hash):
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            audit("LOGIN", "", user.id)
            return redirect(url_for("dashboard"))

        # Deliberately generic message - don't reveal whether the
        # username exists.
        flash("Invalid username or password", "danger")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5/hour")
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email_addr = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not username or not password:
            flash("Username and password are required.", "danger")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
        elif password != confirm:
            flash("Passwords do not match.", "danger")
        elif User.query.filter_by(username=username).first():
            flash("That username is already taken.", "danger")
        else:
            user = User(
                username=username,
                email=email_addr or None,
                password_hash=generate_password_hash(password),
                role="USER",
                is_admin=False,
                active=True,
            )
            db.session.add(user)
            db.session.commit()

            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            audit("REGISTER", "", user.id)

            flash("Account created. Connect your mailbox to get started.", "success")
            return redirect(url_for("mail_accounts_page"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/")
@login_required
def dashboard():
    user = current_user()
    uid = user.id

    accounts = MailAccount.query.filter_by(user_id=uid).all()

    emails = (
        Email.query.filter_by(user_id=uid)
        .order_by(Email.received_at.desc())
        .limit(100)
        .all()
    )

    stats = {
        "total": Email.query.filter_by(user_id=uid).count(),
        "unread": Email.query.filter_by(user_id=uid, is_read=False).count(),
        "urgent": Email.query.filter(
            Email.user_id == uid, Email.priority.in_(["URGENT", "CRITICAL"])
        ).count(),
        "actions": Email.query.filter_by(user_id=uid, action_required=True).count(),
        "drafts": Draft.query.join(Email).filter(
            Email.user_id == uid, Draft.status == "DRAFT"
        ).count(),
        "tasks": Task.query.filter(Task.user_id == uid, Task.status != "DONE").count(),
        "vendors": Vendor.query.count(),
    }

    return render_template(
        "dashboard.html", emails=emails, stats=stats, mail_accounts=accounts
    )


# ============================================================
# MAIL SYNC
# ============================================================

@app.route("/sync")
@login_required
def sync():
    user = current_user()

    accounts = MailAccount.query.filter_by(user_id=user.id, active=True).all()

    if not accounts:
        flash("Connect a mailbox first.", "warning")
        return redirect(url_for("mail_accounts_page"))

    account_id = request.args.get("account_id")
    if account_id:
        accounts = [a for a in accounts if str(a.id) == account_id]
        if not accounts:
            flash("Mailbox not found.", "danger")
            return redirect(url_for("dashboard"))

    limit = int(request.args.get("limit", os.getenv("SYNC_LIMIT", "100")))
    scope = request.args.get("scope", "inbox")

    total_added = total_updated = 0
    errors = []

    for account in accounts:
        try:
            ms = get_mail_service(account)
            msgs = ms.fetch_recent(limit, scope)
            added = updated = 0

            for m in msgs:
                existing = Email.query.filter_by(
                    mail_account_id=account.id, provider_message_id=m["id"]
                ).first()
                if existing:
                    existing.is_read = m.get("is_read", existing.is_read)
                    updated += 1
                    continue

                e = Email(
                    mail_account_id=account.id,
                    user_id=user.id,
                    provider=account.provider,
                    provider_message_id=m["id"],
                    thread_id=m.get("thread_id"),
                    conversation_id=m.get("conversation_id"),
                    sender=m.get("sender", ""),
                    recipients=m.get("recipients", ""),
                    cc=m.get("cc", ""),
                    subject=m.get("subject", ""),
                    body=m.get("body", ""),
                    received_at=m.get("received_at"),
                    is_read=m.get("is_read", False),
                    folder=m.get("folder", "Inbox"),
                )
                db.session.add(e)
                db.session.flush()

                for a in m.get("attachments", []):
                    db.session.add(Attachment(
                        email_id=e.id,
                        provider_attachment_id=a.get("id"),
                        filename=a.get("filename"),
                        content_type=a.get("content_type"),
                        size_bytes=a.get("size_bytes", 0),
                        storage_path=a.get("storage_path"),
                        extracted_text=a.get("extracted_text", ""),
                    ))
                added += 1

            db.session.commit()
            total_added += added
            total_updated += updated
            audit(
                "SYNC",
                f"{account.provider}:{account.email_address} {scope}: added={added}, updated={updated}",
                user.id,
            )

        except Exception as exc:
            db.session.rollback()
            log.exception("Mail sync failed for account id=%s", account.id)
            errors.append(f"{account.email_address or account.provider}: {exc}")

    if errors:
        flash("Sync completed with errors: " + "; ".join(errors), "warning")
    else:
        flash(f"Sync completed: {total_added} new, {total_updated} existing.", "success")

    return redirect(url_for("dashboard"))


# ============================================================
# AI ANALYSIS
# ============================================================

def analyze_one(e):
    attachment_text = "\n\n".join(
        a.extracted_text for a in e.attachments if a.extracted_text
    )

    d = analyze_email(e.subject, e.body, attachment_text)

    a = AIAnalysis.query.filter_by(email_id=e.id).first()
    if not a:
        a = AIAnalysis(email_id=e.id)

    a.category = d.get("category", "General")
    a.priority = d.get("priority", "MEDIUM")
    a.summary = d.get("summary", "")
    a.action_required = d.get("action_required", False)
    a.action = d.get("action", "")
    a.deadline = d.get("deadline", "")
    a.sentiment = d.get("sentiment", "Neutral")
    a.reply_required = d.get("reply_required", False)
    a.confidence = d.get("confidence", 0)
    a.entities = json.dumps(d.get("entities", []))
    a.risk_level = d.get("risk_level", "LOW")
    a.recommended_route = d.get("recommended_route", "Manual Review")

    e.category = a.category
    e.priority = a.priority
    e.summary = a.summary
    e.action_required = a.action_required
    e.risk_level = a.risk_level
    e.ai_processed = True

    db.session.add(a)

    if a.action_required:
        existing_task = Task.query.filter_by(email_id=e.id, status="OPEN").first()
        if not existing_task:
            db.session.add(Task(
                email_id=e.id,
                user_id=e.user_id,
                title=a.action or "Review email action",
                description=a.summary or e.subject,
                priority=a.priority or "MEDIUM",
                due_date=Task.parse_due_date(a.deadline),
            ))

    return d


@app.route("/analyze-bulk", methods=["POST"])
@login_required
def bulk():
    user = current_user()
    ok = err = 0
    limit = int(request.form.get("limit", 20))

    emails = (
        Email.query.filter_by(user_id=user.id, ai_processed=False)
        .order_by(Email.received_at.desc())
        .limit(limit)
        .all()
    )

    log.info("Bulk AI analysis starting: %d emails queued", len(emails))

    for e in emails:
        try:
            analyze_one(e)
            process_rules(e)
            db.session.commit()
            ok += 1
        except Exception:
            db.session.rollback()
            err += 1
            log.exception("AI analysis failed for email id=%s", e.id)

    audit("AI_BULK", f"completed={ok}, errors={err}", user.id)
    flash(f"AI analysis: {ok} completed, {err} failed.", "success" if not err else "warning")
    return redirect(url_for("dashboard"))


@app.route("/email/<int:id>")
@login_required
def email_view(id):
    e = owned_email_or_404(id)
    a = AIAnalysis.query.filter_by(email_id=id).first()
    drafts = Draft.query.filter_by(email_id=id).order_by(Draft.created_at.desc()).all()
    tasks = Task.query.filter_by(email_id=id).all()
    thread = (
        Email.query.filter(Email.thread_id == e.thread_id, Email.user_id == e.user_id)
        .order_by(Email.received_at.asc())
        .all()
        if e.thread_id else [e]
    )
    return render_template(
        "email.html", email=e, analysis=a, drafts=drafts, tasks=tasks, thread=thread
    )


@app.post("/email/<int:id>/analyze")
@login_required
def analyze(id):
    e = owned_email_or_404(id)
    try:
        analyze_one(e)
        db.session.commit()
        audit("AI_ANALYZE", f"email={id}", session["user_id"], id)
        flash("Groq analysis completed", "success")
    except Exception as exc:
        db.session.rollback()
        log.exception("AI analysis failed for email id=%s", id)
        flash(f"AI analysis failed: {exc}", "danger")
    return redirect(url_for("email_view", id=id))


@app.post("/email/<int:id>/draft")
@login_required
def draft(id):
    e = owned_email_or_404(id)
    try:
        txt = "\n\n".join(a.extracted_text for a in e.attachments if a.extracted_text)
        body = generate_reply(e.sender, e.subject, e.body, txt)
        subject = ("Re: " + e.subject) if not e.subject.lower().startswith("re:") else e.subject
        d = Draft(email_id=id, recipient=e.sender, subject=subject, body=body)
        db.session.add(d)
        db.session.commit()
        audit("DRAFT_CREATE", f"draft={d.id}", session["user_id"], id)
    except Exception as exc:
        db.session.rollback()
        log.exception("Draft generation failed for email id=%s", id)
        flash(f"Draft generation failed: {exc}", "danger")
    return redirect(url_for("email_view", id=id))


@app.post("/draft/<int:id>/save")
@login_required
def save_draft(id):
    d = Draft.query.get_or_404(id)
    owned_email_or_404(d.email_id)
    d.subject = request.form.get("subject", d.subject)
    d.body = request.form.get("body", d.body)
    db.session.commit()
    return redirect(url_for("email_view", id=d.email_id))


@app.post("/draft/<int:id>/send")
@login_required
def send(id):
    d = Draft.query.get_or_404(id)
    e = owned_email_or_404(d.email_id)
    try:
        account = MailAccount.query.get(e.mail_account_id)
        if not account:
            raise RuntimeError("The mailbox this email came from is no longer connected.")

        attachments = (
            Attachment.query.filter_by(email_id=d.email_id).all()
            if request.form.get("include_attachments") else []
        )
        get_mail_service(account).send_email(d.recipient, d.subject, d.body, attachments)
        d.status = "SENT"
        d.sent_at = datetime.utcnow()
        db.session.commit()
        audit("SEND", f"draft={id}", session["user_id"], d.email_id)
        flash("Email sent", "success")
    except Exception as exc:
        db.session.rollback()
        log.exception("Send failed for draft id=%s", id)
        flash(f"Send failed: {exc}", "danger")
    return redirect(url_for("email_view", id=d.email_id))


# ============================================================
# TASKS / VENDORS / SEARCH / AUDIT
# ============================================================

@app.route("/tasks")
@login_required
def tasks():
    user = current_user()
    return render_template(
        "tasks.html",
        tasks=Task.query.filter_by(user_id=user.id)
        .order_by(Task.status.asc(), Task.due_date.asc())
        .all(),
    )


@app.post("/task/<int:id>/done")
@login_required
def done(id):
    t = Task.query.get_or_404(id)
    if t.user_id != session.get("user_id"):
        abort(404)
    t.status = "DONE"
    t.completed_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("tasks"))


@app.route("/vendors")
@login_required
def vendors():
    # Shared company-wide reference data, visible to every logged-in user.
    return render_template(
        "vendors.html", vendors=Vendor.query.order_by(Vendor.name.asc()).all()
    )


@app.post("/vendors/add")
@login_required
def add_vendor():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Vendor name is required.", "danger")
        return redirect(url_for("vendors"))

    db.session.add(Vendor(
        name=name,
        email=request.form.get("email", "").strip(),
        domain=request.form.get("domain", "").strip().lower(),
        phone=request.form.get("phone", "").strip(),
        notes=request.form.get("notes", "").strip(),
    ))
    db.session.commit()
    return redirect(url_for("vendors"))


@app.route("/search")
@login_required
def search():
    user = current_user()
    q = request.args.get("q", "")
    rows = []
    if q:
        term = "%" + q + "%"
        rows = (
            Email.query.filter(
                Email.user_id == user.id,
                or_(Email.subject.ilike(term), Email.sender.ilike(term), Email.body.ilike(term)),
            )
            .order_by(Email.received_at.desc())
            .limit(200)
            .all()
        )
    return render_template("search.html", q=q, emails=rows)


@app.route("/audit")
@login_required
@admin_required
def logs():
    return render_template(
        "audit.html", logs=AuditLog.query.order_by(AuditLog.created_at.desc()).limit(500).all()
    )


# ============================================================
# SETTINGS / ADMIN / HEALTH
# ============================================================

@app.route("/settings")
@login_required
def settings():
    user = current_user()

    all_users = None
    if user.is_admin:
        all_users = User.query.order_by(User.created_at.asc()).all()

    return render_template(
        "settings.html",
        groq=bool(os.getenv("GROQ_API_KEY")),
        scheduler=os.getenv("SCHEDULER_ENABLED", "0") == "1",
        mail_account_count=MailAccount.query.filter_by(user_id=user.id).count(),
        all_users=all_users,
    )


@app.post("/admin/users/create")
@login_required
@admin_required
def admin_create_user():
    username = request.form.get("username", "").strip()
    email_addr = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    make_admin = bool(request.form.get("is_admin"))

    if not username or not password:
        flash("Username and password are required.", "danger")
    elif len(password) < 8:
        flash("Password must be at least 8 characters.", "danger")
    elif User.query.filter_by(username=username).first():
        flash("That username is already taken.", "danger")
    else:
        db.session.add(User(
            username=username,
            email=email_addr or None,
            password_hash=generate_password_hash(password),
            role="ADMIN" if make_admin else "USER",
            is_admin=make_admin,
            active=True,
        ))
        db.session.commit()
        audit("ADMIN_CREATE_USER", f"username={username}", session["user_id"])
        flash(f"Account created for {username}.", "success")

    return redirect(url_for("settings"))


@app.post("/admin/users/<int:id>/toggle-active")
@login_required
@admin_required
def admin_toggle_active(id):
    target = User.query.get_or_404(id)
    current = current_user()

    if target.id == current.id:
        flash("You can't deactivate your own account.", "danger")
        return redirect(url_for("settings"))

    if target.active and target.is_admin:
        remaining_admins = User.query.filter_by(is_admin=True, active=True).count()
        if remaining_admins <= 1:
            flash("Can't deactivate the last remaining admin.", "danger")
            return redirect(url_for("settings"))

    target.active = not target.active
    db.session.commit()
    audit("ADMIN_TOGGLE_ACTIVE", f"user={target.username} active={target.active}", session["user_id"])
    return redirect(url_for("settings"))


@app.post("/admin/users/<int:id>/toggle-admin")
@login_required
@admin_required
def admin_toggle_admin(id):
    target = User.query.get_or_404(id)
    current = current_user()

    if target.id == current.id:
        flash("You can't change your own admin status.", "danger")
        return redirect(url_for("settings"))

    if target.is_admin:
        remaining_admins = User.query.filter_by(is_admin=True, active=True).count()
        if remaining_admins <= 1:
            flash("Can't remove the last remaining admin.", "danger")
            return redirect(url_for("settings"))

    target.is_admin = not target.is_admin
    target.role = "ADMIN" if target.is_admin else "USER"
    db.session.commit()
    audit("ADMIN_TOGGLE_ADMIN", f"user={target.username} is_admin={target.is_admin}", session["user_id"])
    return redirect(url_for("settings"))


@app.route("/health")
def health():
    return jsonify(
        status="ok",
        groq_configured=bool(os.getenv("GROQ_API_KEY")),
    )


# ============================================================
# MAIL ACCOUNTS (per-user Gmail / Outlook mailbox connections)
# ============================================================

@app.route("/mail-accounts")
@login_required
def mail_accounts_page():
    user = current_user()
    accounts = (
        MailAccount.query.filter_by(user_id=user.id)
        .order_by(MailAccount.created_at.desc())
        .all()
    )
    return render_template(
        "mail_accounts.html",
        accounts=accounts,
        outlook_configured=bool(os.getenv("OUTLOOK_CLIENT_ID")),
    )


@app.route("/mail-accounts/<int:id>/disconnect", methods=["POST"])
@login_required
def mail_account_disconnect(id):
    account = MailAccount.query.get_or_404(id)
    if account.user_id != session.get("user_id"):
        abort(404)
    db.session.delete(account)
    db.session.commit()
    audit("MAIL_ACCOUNT_DISCONNECT", f"account={id}", session["user_id"])
    flash("Mailbox disconnected.", "success")
    return redirect(url_for("mail_accounts_page"))


# --- Gmail mailbox connection (separate from the Drive connection) ---

@app.route("/mail-accounts/google/login")
@login_required
def mail_google_login():
    redirect_uri = url_for("mail_google_callback", _external=True)
    flow = create_gmail_oauth_flow(redirect_uri=redirect_uri)

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    session["mail_google_oauth_state"] = state
    session["mail_google_code_verifier"] = flow.code_verifier

    return redirect(authorization_url)


@app.route("/mail-accounts/google/callback")
@login_required
def mail_google_callback():
    user = current_user()

    try:
        saved_state = session.get("mail_google_oauth_state")
        saved_code_verifier = session.get("mail_google_code_verifier")
        returned_state = request.args.get("state")

        if not saved_state or not saved_code_verifier:
            flash("Google OAuth session expired. Please try again.", "danger")
            return redirect(url_for("mail_accounts_page"))

        if returned_state != saved_state:
            flash("Invalid Google OAuth state. Please try again.", "danger")
            return redirect(url_for("mail_accounts_page"))

        if request.args.get("error"):
            flash(f"Google authorization failed: {request.args.get('error')}", "danger")
            return redirect(url_for("mail_accounts_page"))

        redirect_uri = url_for("mail_google_callback", _external=True)
        flow = create_gmail_oauth_flow(
            redirect_uri=redirect_uri,
            code_verifier=saved_code_verifier,
            state=saved_state,
        )
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials

        google_user = get_google_user_info(credentials)
        google_email = google_user.get("email")

        account = MailAccount.query.filter_by(
            user_id=user.id, provider="gmail", email_address=google_email
        ).first()
        if not account:
            account = MailAccount(user_id=user.id, provider="gmail", email_address=google_email)

        account.access_token = credentials.token
        account.refresh_token = credentials.refresh_token or account.refresh_token
        account.token_uri = credentials.token_uri
        account.client_id = credentials.client_id
        account.client_secret = credentials.client_secret
        account.scopes = " ".join(credentials.scopes or [])
        account.expires_at = credentials.expiry
        account.active = True

        db.session.add(account)
        db.session.commit()

        audit("MAIL_ACCOUNT_CONNECT", f"gmail:{google_email}", user.id)
        flash(f"Gmail mailbox connected: {google_email}", "success")

    except Exception as exc:
        db.session.rollback()
        log.exception("Gmail mailbox connection failed")
        flash(f"Gmail connection failed: {exc}", "danger")

    finally:
        session.pop("mail_google_oauth_state", None)
        session.pop("mail_google_code_verifier", None)

    return redirect(url_for("mail_accounts_page"))


# --- Outlook mailbox connection ---

@app.route("/mail-accounts/outlook/login")
@login_required
def mail_outlook_login():
    try:
        redirect_uri = url_for("mail_outlook_callback", _external=True)
        flow = initiate_outlook_flow(redirect_uri)
        session["outlook_auth_flow"] = flow
        return redirect(flow["auth_uri"])
    except Exception as exc:
        log.exception("Failed to start Outlook OAuth flow")
        flash(f"Outlook connection failed: {exc}", "danger")
        return redirect(url_for("mail_accounts_page"))


@app.route("/mail-accounts/outlook/callback")
@login_required
def mail_outlook_callback():
    user = current_user()
    flow = session.get("outlook_auth_flow")

    if not flow:
        flash("Outlook OAuth session expired. Please try again.", "danger")
        return redirect(url_for("mail_accounts_page"))

    try:
        result = complete_outlook_flow(flow, request.args.to_dict())
        claims = result.get("id_token_claims", {}) or {}
        email_addr = claims.get("preferred_username") or claims.get("email") or "Outlook account"
        expires_in = result.get("expires_in", 3600)

        account = MailAccount.query.filter_by(
            user_id=user.id, provider="outlook", email_address=email_addr
        ).first()
        if not account:
            account = MailAccount(user_id=user.id, provider="outlook", email_address=email_addr)

        account.access_token = result["access_token"]
        account.refresh_token = result.get("refresh_token", account.refresh_token)
        account.expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        account.active = True

        db.session.add(account)
        db.session.commit()

        audit("MAIL_ACCOUNT_CONNECT", f"outlook:{email_addr}", user.id)
        flash(f"Outlook mailbox connected: {email_addr}", "success")

    except Exception as exc:
        db.session.rollback()
        log.exception("Outlook mailbox connection failed")
        flash(f"Outlook connection failed: {exc}", "danger")

    finally:
        session.pop("outlook_auth_flow", None)

    return redirect(url_for("mail_accounts_page"))


# ============================================================
# CLOUD STORAGE (Google Drive)
# ============================================================

@app.route("/cloud-storage")
@login_required
def cloud_storage():
    user = current_user()
    connection = GoogleConnection.query.filter_by(user_id=user.id).first()
    google_connected = connection is not None and bool(connection.google_email)

    files = []
    search_term = request.args.get("q", "").strip()

    if google_connected:
        try:
            files = list_drive_files(user, search_term)
        except Exception as exc:
            log.exception("Google Drive listing failed for user id=%s", user.id)
            flash(f"Google Drive error: {exc}", "danger")

    return render_template(
        "cloud_storage.html",
        user=user,
        google_connected=google_connected,
        files=files,
        search=search_term,
    )


@app.route("/google/login")
@login_required
def google_login():
    redirect_uri = url_for("google_callback", _external=True)
    flow = create_oauth_flow(redirect_uri=redirect_uri)

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    session["google_oauth_state"] = state
    session["google_code_verifier"] = flow.code_verifier

    return redirect(authorization_url)


@app.route("/google/callback")
@login_required
def google_callback():
    try:
        user = current_user()
        if not user:
            flash("Application login session expired. Please login again.", "danger")
            return redirect(url_for("login"))

        saved_state = session.get("google_oauth_state")
        saved_code_verifier = session.get("google_code_verifier")
        returned_state = request.args.get("state")

        if not saved_state or not saved_code_verifier:
            flash("Google OAuth session expired. Please try again.", "danger")
            return redirect(url_for("cloud_storage"))

        if returned_state != saved_state:
            flash("Invalid Google OAuth state. Please try again.", "danger")
            session.pop("google_oauth_state", None)
            session.pop("google_code_verifier", None)
            return redirect(url_for("cloud_storage"))

        if request.args.get("error"):
            error = request.args.get("error")
            session.pop("google_oauth_state", None)
            session.pop("google_code_verifier", None)
            flash(f"Google authorization failed: {error}", "danger")
            return redirect(url_for("cloud_storage"))

        redirect_uri = url_for("google_callback", _external=True)
        flow = create_oauth_flow(
            redirect_uri=redirect_uri,
            code_verifier=saved_code_verifier,
            state=saved_state,
        )
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials

        google_user = get_google_user_info(credentials)
        google_email = google_user.get("email")
        google_user_id = google_user.get("id")

        save_connection_from_credentials(user, credentials, google_email, google_user_id)

        session.pop("google_oauth_state", None)
        session.pop("google_code_verifier", None)

        flash(f"Google Drive connected successfully: {google_email}", "success")
        return redirect(url_for("cloud_storage"))

    except Exception as exc:
        db.session.rollback()
        session.pop("google_oauth_state", None)
        session.pop("google_code_verifier", None)
        log.exception("Google connection failed")
        flash(f"Google connection failed: {exc}", "danger")
        return redirect(url_for("cloud_storage"))


@app.route("/google/disconnect")
@login_required
def google_disconnect():
    user = current_user()
    try:
        disconnect_google_drive(user)
        flash("Google Drive disconnected.", "success")
    except Exception as exc:
        log.exception("Google Drive disconnect failed for user id=%s", user.id)
        flash(f"Disconnect failed: {exc}", "danger")
    return redirect(url_for("cloud_storage"))


@app.post("/cloud-storage/summarize/<file_id>")
@login_required
def summarize_drive_file(file_id):
    user = current_user()
    try:
        metadata, data = download_file_bytes(user, file_id)
        filename = metadata.get("name", "Document")
        mime_type = metadata.get("mimeType", "")

        text = extract_text(data, filename, mime_type)
        if not text.strip():
            raise RuntimeError("No readable text was found in this file.")

        result = summarize_text(text, filename)
        return render_template(
            "document_summary.html", filename=filename, mime_type=mime_type, result=result
        )

    except Exception as exc:
        log.exception("Document summarization failed for file_id=%s", file_id)
        flash(f"Document summarization failed: {exc}", "danger")
        return redirect(url_for("cloud_storage"))


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=DEBUG,
    )
