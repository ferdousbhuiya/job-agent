# AI Job Application Bot

Automated Gmail poller and application customizer. This bot scans a Gmail account for job-related emails, uses Groq's LLM to tailor a resume and cover letter to the job description, then sends the documents to a Telegram chat for one-tap approval before sending the application via SMTP.

## Features

- **Gmail Polling**: Scans for unread, job-related emails.
- **AI-Powered Tailoring**: Uses Groq (LLaMA 3.3 70b) to rewrite a master resume and cover letter to match keywords and requirements in the job description.
- **Telegram Control Center**: Presents the tailored documents in a Telegram chat with "Approve" and "Skip" buttons.
- **Recipient Extraction**: Attempts to find the application recipient's email address from the job description text.
- **PDF Conversion**: Converts final .docx documents to .PDF using LibreOffice before sending.
- **SMTP Sending**: Mails the application with PDF attachments using a Gmail account.
- **State Management**: Remembers processed emails in `state.json` to prevent duplicates.

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

## Deployment (keep it running 24/7)

This is a long-running Telegram bot, NOT a Streamlit web app. Streamlit Cloud
cannot keep it alive. Use an always-on host such as **Railway**, **Render**,
or **Fly.io**.

**Important — PDF conversion:** `sender.py` uses LibreOffice (`soffice`) to turn
`.docx` into `.pdf`. Make sure your host has it installed, or set the application
to skip PDFs. On a Linux host:
`apt-get install -y libreoffice-core libreoffice-writer` (or use `docx2pdf`).

### Railway
1. Push this repo to GitHub and import it in the Railway dashboard.
2. Set the following environment variables (Variable -> Add):
   `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, `YOUR_NAME`, `POLL_INTERVAL_MIN` (default 15).
3. `railway.json` already sets the start command. Railway restarts on failure.

### Render
1. Create a **Background Worker** (not Web Service) pointing at this repo.
2. Render auto-uses `render.yaml`. Set the same env vars as above as secret values.
3. The worker runs `python runner.py` with LibreOffice if you install it in the image.

### Run on your own machine (no cloud)
```sh
python runner.py --interval 15
```
Requires LibreOffice for PDF attachments. Keep the machine on.
