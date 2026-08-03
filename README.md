# AI Job Application Bot

Automated Gmail poller and application customizer. This bot scans a Gmail account for job-related emails, uses Groq's LLM to tailor a resume and cover letter to the job description, then sends the documents to a Telegram chat for one-tap approval before sending the application via SMTP.

## Features

- **Gmail Polling**: Scans for unread, job-related emails.
- **AI-Powered Tailoring**: Uses Groq (LLaMA 3.3 70b) to rewrite a master resume and cover letter to match keywords and requirements in the job description.
- **Telegram Control Center**: Presents the tailored documents in a Telegram chat with "Approve" and "Skip" buttons.
- **Recipient Extraction**: Attempts to find the application recipient's email address from the job description text.
- **PDF Conversion**: Converts final .docx documents to .PDF using LibreOffice before sending.
- **SMTP Sending**: Mails the application with PDF attachments using a Gmail account.
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
           [Sender] builds PDFs, SMTP sends application
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
    - `GMAIL_APP_PASSWORD`: A [16-digit Google App Password](https://support.google.com/accounts/answer/185833) for your Gmail account.
    - `GROQ_API_KEY`: Your API key from [Groq Console](https://console.groq.com/keys).
    - `TELEGRAM_BOT_TOKEN`: Get this from `@BotFather` on Telegram.
    - `TELEGRAM_CHAT_ID`: Your numeric user ID. You can get this from a bot like `@userinfobot`.
    - `YOUR_NAME`: Your full name, for email signatures.
    - `RESEND_API_KEY`: API key from [Resend](https://resend.com/api-keys) — used
      to send application emails (Gmail SMTP is blocked on Render free workers).
    - `RESEND_FROM` (Optional): Verified sender on Resend; defaults to `GMAIL_ADDRESS`.
    - `POLL_INTERVAL_MIN`: How often to check Gmail (e.g., `15` for every 15 minutes).
    - `LIBREOFFICE_PATH` (Optional): Full path to `soffice.exe` if not in your system PATH.

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

## Deployment (keep it running 24/7 on Render)

This is a long-running Telegram bot, NOT a Streamlit web app. Streamlit Cloud
cannot keep it alive. The recommended free host is **Render** — free worker
tiers are persistent (they do not expire like Railway's monthly discard). Note:
Render's **free tier puts idle workers to sleep** — send `/scan` (or any message)
to wake it, and it resumes polling.

**Attachments:** the `Dockerfile` installs Python only (lightweight, for fast
deploys). If LibreOffice is absent, the bot sends the tailored documents as
`.docx` attachments. To send PDFs instead, uncomment the LibreOffice install
line in the `Dockerfile`, or set `LIBREOFFICE_PATH` to its executable — the bot
auto-detects it (see `sender.py`).

### Deploy from GitHub to Render (free)
1. Make sure this repo is pushed to GitHub (including `master_resume.docx`, which
   is committed despite the `*.docx` ignore rule).
2. In the Render dashboard: **New → Background Worker**.
3. Connect the GitHub repo. Render auto-reads `render.yaml` + `Dockerfile`.
4. Set these **Environment Variables** (free worker stores them as secrets):
   - `GMAIL_ADDRESS`
   - `GMAIL_APP_PASSWORD`
   - `GROQ_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `YOUR_NAME`
   - `RESEND_API_KEY` — from https://resend.com/api-keys. Used to send application
     emails because Gmail SMTP (465/587) is blocked on Render free workers.
   - `RESEND_FROM` — verified sender on Resend (defaults to `GMAIL_ADDRESS`).
   - `POLL_INTERVAL_MIN` (`15`) — created automatically by `render.yaml`
5. Deploy. Render builds the Docker image (installs Python; LibreOffice optional,
   see the Attachments note above), starts the worker, and keeps it running.
   It restarts on failure.

### Run on your own machine (no cloud)
```sh
python runner.py --interval 15
```
Requires LibreOffice for PDF attachments (`.docx` sent otherwise). Keep the
machine on. Dedupe is server-side via Gmail labels — no local `state.json`, so
restarts re-present any job still awaiting your decision.
