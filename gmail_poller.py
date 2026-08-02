# gmail_poller.py
# Polls Gmail for new job-related emails. Keeps a seen-ID set in state.json
# so nothing is processed twice. Returns only NEW (unprocessed) emails.
import os
import json
import imaplib
import email
import re
from email.header import decode_header
from bs4 import BeautifulSoup
from config import get_key

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

# Keywords that hint an email is a job posting / alert worth handling.
JOB_KEYWORDS = ("job", "vacancy", "intern", "hiring", "opportunity", "position",
                "role", "recruit", "apply", "opening", "career", "talent")


def load_state():
    """Return dict of processed email-IDs -> {subject, from, processed_at}."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def is_processed(state, uid):
    return uid in state


def mark_presented(email_id, subject, sender, sent=False):
    """Record that an email has been handed to the user, so it isn't shown again."""
    state = load_state()
    state[email_id] = {
        "subject": subject,
        "from": sender,
        "sent": sent,
    }
    save_state(state)


def _decode(header_part):
    if not header_part:
        return ""
    text, encoding = decode_header(header_part)[0]
    if isinstance(text, bytes):
        return text.decode(encoding or "utf-8", errors="ignore")
    return text


def _strip_html(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text("\n")
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def _clean_body(msg):
    """Return clean plain-text body (fall back to HTML stripped)."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            dispo = str(part.get("Content-Disposition"))
            if "attachment" in dispo:
                continue
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            if ct == "text/plain":
                return payload.decode(charset, errors="ignore")
            if ct == "text/html":
                return _strip_html(payload.decode(charset, errors="ignore"))
    else:
        ct = msg.get_content_type()
        charset = msg.get_content_charset() or "utf-8"
        body = (msg.get_payload(decode=True) or b"").decode(charset, errors="ignore")
        if ct == "text/html":
            return _strip_html(body)
        return body
    return ""


def _is_job_email(subject, body):
    haystack = f"{subject} {body}".lower()
    return any(k in haystack for k in JOB_KEYWORDS)


def fetch_new_emails(limit=10, job_filter=True):
    """Connect to Gmail, fetch UNSEEN emails, return only new + job-ish ones.

    Returns (new_emails, error). new_emails = list of dicts:
      {id, subject, from, body, date}
    Processed IDs (already in state.json) are skipped.
    """
    gmail_user = get_key("GMAIL_ADDRESS")
    gmail_password = get_key("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_password:
        return [], "Gmail credentials missing"

    state = load_state()
    new_emails = []

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail_user, gmail_password)
        mail.select("inbox")

        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            mail.logout()
            return [], "No unread emails"

        email_ids = messages[0].split()
        latest_ids = email_ids[-limit:][::-1]  # newest first

        for num in latest_ids:
            uid = num.decode()
            if is_processed(state, uid):
                continue
            status, msg_data = mail.fetch(num, "(BODY.PEEK[])")
            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue
                msg = email.message_from_bytes(response_part[1])
                subject = _decode(msg["Subject"])
                sender = _decode(msg.get("From"))
                body = _clean_body(msg)
                date = msg.get("Date", "")

                if not _is_job_email(subject, body):
                    continue

                new_emails.append({
                    "id": uid,
                    "subject": subject or "(no subject)",
                    "from": sender,
                    "body": body,
                    "date": date,
                })

        mail.logout()
    except Exception as e:
        return [], f"IMAP Error: {e}"

    return new_emails, "Success"