# AI Job Application Bot

Automated Gmail poller and application customizer. This bot scans a Gmail account for job-related messages, uses Groq's LLM to tailor a resume and cover letter to the job description, then sends the documents to a Telegram chat for one-tap approval before sending the application.

## Features

- **Gmail Polling**: Time-window scan of recent mail (default 10 days, `SCAN_DAYS`), catches messages you've already read — not just unread.
- **AI-Powered Tailoring**: Uses Groq (LLaMA 3.3 70b) to rewrite a master resume and cover letter to match keywords and requirements in the job description.
- **Telegram Control Center**: Presents the tailored documents in a Telegram chat with "Approve" and "Skip" buttons.
- **Recipient Extraction**: Attempts to find the application recipient's email address from the job description text.
- **Resend Sending**: Sends applications over Resend's REST API (port 443) — works on hosts that block SMTP egress (Render, Hetzner abuse filters).
- **Reply Routing**: Recruiter replies to `applications@yourdomain` are redirected (via `Reply-To`) to your real Gmail inbox.
- **State Management**: Deduplicates via a Gmail label (`Bot/Processed`) — survives restarts with no local storage.

## How it Works

```
[Gmail poller] ──new job email──▶ [AI tailor] resume+cover
                                      │
                                      ▼
[Telegram bot] shows JD + tailored docs + "Approve?"
                                      │
                ┌─────────────────────┴──────────┐
                │  ✅ Approve        ❌ Skip     │
                └─────────────────────┬──────────┘
                                      ▼
           [Sender] builds PDFs/DOCX, Resend sends application
```

If the AI cannot find a recipient email in the job description, the bot will ask you to provide one in the Telegram chat.

## Setup

1.  **Clone & Install Dependencies**:
    ```sh
    git clone ...
    cd email-automation
    python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

2.  **Install LibreOffice**:
    This is required for converting `.docx` files to `.pdf`. Install it from [the official site](https://www.libreoffice.org/download/download/). Make sure the `soffice` command is available in your system's PATH, or set the full path in your `.env` file.

3.  **Create `.env` file**:
    Copy `.env.example` to `.env` and fill in all the required values:
    - `GMAIL_ADDRESS`: Your Gmail account.
    - `GMAIL_APP_PASSWORD`: A [16-digit Google App Password](https://support.google.com/accounts/answer/185833) for your Gmail account (needs 2-Step Verification on).
    - `GROQ_API_KEY`: Your API key from [Groq Console](https://console.groq.com/keys).
    - `TELEGRAM_BOT_TOKEN`: Get this from `@BotFather` on Telegram.
    - `TELEGRAM_CHAT_ID`: Your numeric user ID. You can get this from a bot like `@userinfobot`.
    - `YOUR_NAME`: Your full name, for email signatures.
    - `RESEND_API_KEY`: From [resend.com](https://resend.com) → API Keys. Sends applications over HTTPS (see Deployment).
    - `RESEND_FROM`: A `@yourdomain.com` address on a Resend-**verified** domain. `onboarding@resend.dev` works but only reaches *your own* address.
    - `RESEND_REPLY_TO` (recommended): your real Gmail — receives recruiter replies.
    - `SCAN_DAYS` (optional): how many days back to scan for job mail. Default 10.
    - `POLL_INTERVAL_MIN`: How often to auto-check Gmail (e.g., `15`). `0` = manual `/scan` only.
    - `LIBREOFFICE_PATH` (Optional): Full path to `soffice.exe` if not in your system PATH (PDF attachments).

4.  **Add Master Resume**:
    Place your master resume file in the root directory and name it `master_resume.docx`.

## Usage

Run the bot from your terminal:

```sh
# Poll every 15 minutes (or value from .env)
python runner.py

# Poll every 5 minutes, overriding .env
python runner.py --interval 5

# Run once without auto-polling (manual /scan only)
python runner.py --interval 0
```

Once running, interact with your bot on Telegram:
- `/start`: See a welcome message.
- `/scan`: Manually trigger a Gmail scan.
- `/status`: See how many jobs are awaiting your decision.
- `/help`: Show available commands.

## Deployment (keep it running 24/7)

This is a long-running Telegram bot, not a web app. It needs a persistent host.
Any host works as long as it can reach **https://api.resend.com** (HTTPS
port 443). Outbound SMTP is blocked on many free hosts, which is why sending
goes through **Resend's HTTPS API** instead of Gmail SMTP.

**Recommended host: any always-on VM you control** — Hetzner Cloud (paid, tiny
server, ~€4/mo) or Oracle Cloud Always Free. A plain `systemd` service is all
that's needed (guide below). Render's free workers sleep when idle and block
SMTP, so it's a fallback, not the primary path.

**Attachments:** the `Dockerfile` installs Python only (lightweight, for fast
deploys). If LibreOffice is absent, the bot sends the tailored documents as
`.docx` attachments. To send PDFs instead, uncomment the LibreOffice install
line in the `Dockerfile`, or set `LIBREOFFICE_PATH` — the bot auto-detects it
(see `sender.py`).

### Deploy on a VM with systemd (Hetzner / Oracle)

1. **Provision a VM** with Ubuntu 22.04+ (ships Python 3.10+). Avoid Ubuntu
   20.04 — Python 3.8 can't install `openai` (missing `jiter` wheels).

2. **Install deps + clone:**
    ```bash
    apt update && apt install -y python3-venv python3-pip git curl
    git clone https://github.com/<you>/job-agent.git /opt/job-agent
    cd /opt/job-agent
    python3 -m venv venv
    venv/bin/pip install -r requirements.txt
    ```

3. **Secrets:**
    ```bash
    mkdir -p /etc/job-agent
    nano /etc/job-agent/.env   # paste full .env (same as local)
    ```

4. **systemd service** `/etc/systemd/system/job-agent.service`:
    ```ini
    [Unit]
    Description=AI Job Agent
    After=network-online.target
    Wants=network-online.target
    [Service]
    Type=simple
    WorkingDirectory=/opt/job-agent
    EnvironmentFile=/etc/job-agent/.env
    ExecStart=/opt/job-agent/venv/bin/python /opt/job-agent/runner.py --interval 0
    Restart=always
    RestartSec=5
    [Install]
    WantedBy=multi-user.target
    ```
    Then start + enable on boot:
    ```bash
    systemctl daemon-reload && systemctl enable --now job-agent
    journalctl -u job-agent -f   # live logs
    ```

5. **Resend domain** — verify `RESEND_FROM`'s domain at resend.com/domains.
   Without a verified domain, Resend only sends to *your own* address.

6. **Update after a `git push`:**
    ```bash
    cd /opt/job-agent && git pull && systemctl restart job-agent
    ```

### Deploy to Render (fallback)
1. Push this repo to GitHub (includes `master_resume.docx`, kept despite the
   `*.docx` ignore rule).
2. Render dashboard: **New → Background Worker** → connect repo. It reads
   `render.yaml` + `Dockerfile`, starts the worker, restarts on failure.
3. Set the same env vars as `.env.example` in the Render dashboard (secrets).
4. Note: Render's **free workers sleep when idle** — send `/scan` to wake the
   poll. SMTP is blocked there, so sending uses Resend automatically.

### Run on your own machine (no cloud)
```bash
python runner.py --interval 15
```
Requires LibreOffice for PDF attachments (`.docx` sent otherwise). Keep the
machine on. Dedupe is server-side via Gmail labels — no local `state.json`, so
restarts re-present any job still awaiting your decision.

### Note for buggy SSH terminals
If your SSH client truncates multi-line pastes (services file ends up missing
lines), write the unit with one short `printf` command instead of a heredoc,
then `cat` the file back to verify before restarting:
