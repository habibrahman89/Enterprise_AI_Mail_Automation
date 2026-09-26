# Enterprise AI Mail Automation

**Sync → AI triage → task → human-approved reply.**

A self-hosted mail assistant that connects to your own Gmail or Outlook mailbox, uses AI (via
Groq) to classify and summarize incoming mail, drafts replies for you to review and approve, and
turns action-required emails into tracked tasks — all running locally, under your control, with
no email content stored anywhere but your own database.

Built for teams: every person signs in with their own account and connects their own mailbox.
Nobody sees anybody else's mail.

---

## 1. What it does

| Capability | Description |
|---|---|
| **Multi-user accounts** | Self-service sign-up or admin-created accounts. Each person only ever sees mail from the mailbox(es) *they* connected. |
| **Mailbox sync** | Connect Gmail and/or Outlook (Microsoft 365) per user via OAuth. Pulls inbox, unread, or category-filtered mail. |
| **AI triage** | Every email is classified by category, priority, risk level, sentiment, and whether it needs a reply or action — using Groq's LLM API with structured JSON output. |
| **Attachment intelligence** | Extracts text from PDF, Word, Excel, PowerPoint, CSV, and TXT attachments (optional OCR for images) so the AI can reason about attached documents too. |
| **AI-drafted replies** | Generates a suggested reply for any email — never sent automatically. A human always reviews and clicks Send. |
| **Tasks & follow-ups** | Emails the AI flags as action-required automatically become tracked tasks with priority and due date. |
| **Vendor register** | A shared, company-wide address book for known vendors/contacts. |
| **Google Drive documents** | Connect Drive separately to browse files and generate AI summaries of documents (PDF/DOCX/XLSX/PPTX). |
| **Automation rules** | Rule engine for auto-routing mail based on sender, subject, category, or risk level. |
| **Admin controls** | Admins can create/disable accounts, promote other admins, and view the audit log. Regular users cannot. |

---

## 2. Demo walkthrough (5 minutes)

This is the fastest way to show the whole workflow to someone new.

1. **Sign in.** Use the admin account created from `.env`, or click *Create an account* to make a
   fresh one.
2. **Connect a mailbox.** Go to **Mail Accounts** → *Connect Gmail* (or *Connect Outlook*) and
   authorize with a real mailbox.
3. **Sync mail.** On the Dashboard, pick a folder/limit and click **Sync**. Recent mail (and any
   attachments) is pulled in.
4. **Run AI triage.** Click **Analyze Unprocessed with Groq**. Watch each email get a category,
   priority, risk level, and summary.
5. **Open an email.** Click any row to see the full AI analysis, the thread, and any auto-created
   task.
6. **Generate a reply.** Click **Draft Reply** — the AI writes a suggested response. Edit it
   freely, then click **Send** only when you're satisfied (nothing is ever sent without this
   manual step).
7. **Check Tasks.** Anything the AI flagged as action-required is already waiting in **Tasks**
   with a priority and due date.
8. **(Optional) Cloud Storage.** Connect Google Drive separately and click **Summarize** on any
   document to see AI-generated summaries, key points, and deadlines pulled from the file.
9. **(Admin) Settings.** Show the admin panel — create a second demo user live, so the audience
   sees that their mailbox and the first user's mailbox stay completely separate.

---

## 3. Installation procedure

### Prerequisites

- **Python 3.11** (recommended — the project is tested against it)
- A **Groq API key** — https://console.groq.com
- A **Google Cloud project** with OAuth credentials, if you want Gmail/Drive support
- A **Microsoft Entra app registration**, if you want Outlook support
- Windows, macOS, or Linux — instructions below use Windows `cmd` syntax where it differs

> Run this from a plain local folder (e.g. `C:\Projects\...`), **not** inside a OneDrive/Dropbox/
> Google Drive synced folder — live file syncing during `pip install` and while the app runs can
> corrupt the virtual environment or the local database.

### Step 1 — Get the code and create a virtual environment

```
cd C:\Projects\ai-mail-automation
py -3.11 -m venv .venv
.venv\Scripts\activate
```

On macOS/Linux:
```
python3.11 -m venv .venv
source .venv/bin/activate
```

You should see `(.venv)` at the start of your prompt once it's active.

### Step 2 — Install dependencies

```
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Using `python -m pip` (rather than a bare `pip`) guarantees packages install into *this* virtual
environment, even if you have others on the machine.

### Step 3 — Configure the app

```
copy .env.example .env
```
(macOS/Linux: `cp .env.example .env`)

Open `.env` and set, at minimum:

| Variable | What to set it to |
|---|---|
| `SECRET_KEY` | A random string — generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Credentials for the first admin account (created automatically on first run) |
| `GROQ_API_KEY` | Your Groq API key |

The app **refuses to start** if `SECRET_KEY` or `ADMIN_PASSWORD` are left at their placeholder
values outside of `FLASK_DEBUG=1` — this is intentional, to stop it from ever running in an
insecure default state.

### Step 4 — (Optional) Set up Gmail

1. In [Google Cloud Console](https://console.cloud.google.com), create/select a project.
2. **APIs & Services → Library** — enable the **Gmail API** (and **Drive API** if you'll use
   Cloud Storage too).
3. **APIs & Services → OAuth consent screen (Audience/Branding)** — fill in the required App
   name, support email, and developer contact email. While in *Testing* status, also add every
   Google account you'll test with under **Test users**.
4. **APIs & Services → Credentials** — create an **OAuth 2.0 Client ID** of type **Web
   application**. Add these Authorized redirect URIs (adjust host/port if different):
   ```
   http://127.0.0.1:5000/google/callback
   http://127.0.0.1:5000/mail-accounts/google/callback
   ```
5. Download the client secret JSON and point `.env`'s `GOOGLE_CLIENT_SECRETS_FILE` at it.
6. Since this runs over plain HTTP on localhost, also set `OAUTH_INSECURE_TRANSPORT=1` in `.env`
   (safe only because it's localhost — never enable this on a network-reachable server).

> **Note:** while the Google app is in *Testing* status, Google expires refresh tokens after
> about 7 days. If a connected mailbox starts failing to sync with `invalid_grant: Token has
> been expired or revoked`, just disconnect and reconnect it from **Mail Accounts**. Click
> **Publish app** on the Audience page once you're past the demo stage to stop this recurring.

### Step 5 — (Optional) Set up Outlook

1. In [Entra admin center](https://entra.microsoft.com), register a new app (public client).
2. Add redirect URI: `http://127.0.0.1:5000/mail-accounts/outlook/callback`
3. Add delegated Microsoft Graph permissions: `Mail.ReadWrite`, `Mail.Send`, `User.Read`,
   `offline_access`.
4. Put the Application (client) ID in `.env` as `OUTLOOK_CLIENT_ID`, and the tenant (`common` for
   personal + work/school accounts, or your tenant ID) as `OUTLOOK_TENANT_ID`.

### Step 6 — Run it

```
python app.py
```
or on Windows: `run_windows.bat`

Open **http://127.0.0.1:5000** in your browser. Log in with the admin credentials from `.env`,
or register a new account.

---

## 4. Everyday use after install

- To add teammates: either they self-register at `/register`, or an admin creates their account
  from **Settings**.
- Each person connects their own mailbox from **Mail Accounts** — this is independent per user.
- Optional background sync: set `SCHEDULER_ENABLED=1` in `.env` to sync automatically on an
  interval instead of clicking Sync manually.

---

## 5. Troubleshooting quick reference

| Symptom | Likely cause / fix |
|---|---|
| `ModuleNotFoundError` on startup | Dependencies installed into the wrong (or no) virtual environment. Confirm `(.venv)` is active and re-run `python -m pip install -r requirements.txt`. |
| `no such column: user.is_admin` (or similar SQL error) | You're running against an old database file from a previous version. Delete `enterprise_mail_ai.db` (and `instance/enterprise_mail_ai.db` if present) and restart — a fresh one is created automatically. |
| Google `redirect_uri_mismatch` | The exact URL the app sent isn't registered in Google Cloud Console → Credentials → your OAuth client → Authorized redirect URIs. Check scheme, host, port, and path match exactly, and allow a few minutes after saving for it to propagate. |
| Google `Gmail API has not been used in project ... or it is disabled` | Enable the Gmail API for your project at the link in the error message, then wait a couple of minutes and retry. |
| Google `invalid_grant: Token has been expired or revoked` | Refresh token expired (common for apps still in Testing status — Google expires them after ~7 days) or access was manually revoked. Disconnect and reconnect that mailbox from **Mail Accounts**. |
| Gmail sync fails with `rateLimitExceeded` / `quotaExceeded` | Gmail's per-minute quota was hit during a large sync. The app automatically retries with backoff; if it still fails, wait a minute and sync a smaller batch. |
| Styling/Bootstrap doesn't load | Check the browser console for Content-Security-Policy blocks — the app only allows scripts/styles from itself and specific CDNs by design. |
| CSRF "token missing/invalid" on a form | Session expired or cookies blocked — log in again. |

---

## 6. Security notes

- CSRF protection on every form, security response headers, hardened session cookies, and login
  rate limiting are enabled by default.
- Per-user data isolation is enforced at the database query level — one user can never view
  another user's synced mail, drafts, or tasks, even by guessing a URL.
- AI replies are **never** auto-sent — a human always reviews and clicks Send.
- Treat AI classification (priority, risk level, action-required) as advisory, not authoritative;
  the email content it reasons over is untrusted input.
- This is a strong local/departmental tool, not a certified enterprise SaaS product. Before any
  internet-facing deployment, add HTTPS, a production WSGI server, a real migration tool
  (Alembic), a production database (PostgreSQL), encrypted token storage, malware scanning on
  attachments, and organizational sign-off for sending mail content to an external AI provider.

---

## 7. Project layout

```
app.py                     Flask app, routes, auth, admin panel
models.py                  Database models (User, MailAccount, Email, Task, ...)
services/
  ai_service.py             Groq-based classification, summarization, reply drafting
  gmail_service.py           Gmail API sync/send + OAuth flow (per mail account)
  outlook_service.py         Microsoft Graph sync/send
  outlook_oauth_service.py   Outlook OAuth (MSAL, auth-code + PKCE)
  google_drive_service.py    Google Drive browsing/download (Cloud Storage tab)
  google_oauth_service.py    Shared Google OAuth (PKCE) helper
  document_service.py        Document text extraction for Drive summaries
  attachment_service.py      Email attachment storage + text extraction
  automation_service.py      Rule engine for auto-routing mail
  audit_service.py           Audit log writer
  scheduler_service.py       Optional background sync
templates/                  All HTML pages (Jinja2 + Bootstrap)
static/js/app.js            Small helper script (row navigation, confirm dialogs)
```

## Author

Habib Rahman

Enterprise AI Mail Automation
