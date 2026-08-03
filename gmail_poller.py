# gmail_poller.py
# Polls Gmail for new job-related emails. Deduplication is handled server-side
# with Gmail labels (Bot/Processed), so it survives container restarts where the
# local filesystem is ephemeral (e.g. Render free worker). Returns only NEW
# (unprocessed) emails.
import re
import imaplib
import email
from contextlib import contextmanager
from email.header import decode_header
from bs4 import BeautifulSoup
from config import get_key

# Label tree (created on first use):
#   Bot/Processed  -> handed to user; tombstone, never re-presented
#   Bot/Flagged    -> presented & awaiting user decision (startup rehydrate)
#   Bot/Sent       -> application sent
LABEL_FLAGGED = "Bot/Flagged"
LABEL_SENT = "Bot/Sent"
LABEL_PROCESSED = "Bot/Processed"

# Keywords that hint an email is a job posting / alert worth handling.
JOB_KEYWORDS = ("job", "vacancy", "intern", "hiring", "opportunity", "position",
                "role", "recruit", "apply", "opening", "career", "talent")

_UID_RE = re.compile(rb"UID (\d+)")


@contextmanager
def connect_gmail():
    """Yield a logged-in, INBOX-selected IMAP connection; always closes it."""
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        mail.login(get_key("GMAIL_ADDRESS"), get_key("GMAIL_APP_PASSWORD"))
        mail.select("INBOX")
        yield mail
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def ensure_labels(mail):
    """Create the Bot label tree. Idempotent — a 'NO' on repeat is fine."""
    for label in ("Bot", LABEL_PROCESSED, LABEL_FLAGGED, LABEL_SENT):
        try:
            mail.create(f'"{label}"')
        except Exception:
            pass


def set_labels(mail, email_id, labels):
    """Add one or more labels to a message by UID. Idempotent."""
    joined = " ".join(f'"{l}"' for l in labels)
    mail.uid("STORE", email_id, "+X-GM-LABELS", f"({joined})")


def clear_labels(mail, email_id, labels):
    """Remove one or more labels from a message by UID."""
    joined = " ".join(f'"{l}"' for l in labels)
    mail.uid("STORE", email_id, "-X-GM-LABELS", f"({joined})")


def mark_presented(email_id, subject, sender, sent=False, flag=False):
    """Record how an email was handled, via Gmail labels.

    email_id is the message UID (string). Labels apply by UID, so this works
    regardless of the selected mailbox. Idempotent — safe to call repeatedly.

    Label semantics:
      Bot/Processed  always set — tombstone, stops re-fetch on /scan.
      Bot/Flagged    set iff flag=True (presented, awaiting decision).
      Bot/Sent       set iff sent=True.
    Clearing Flagged marks the job answered/abandoned so startup rehydration
    never re-presents it.
    """
    labels = [LABEL_PROCESSED]
    if flag:
        labels.append(LABEL_FLAGGED)
    if sent:
        labels.append(LABEL_SENT)
    with connect_gmail() as mail:
        ensure_labels(mail)
        set_labels(mail, email_id, labels)
        if not flag:
            # Answered (sent) or skipped: clear the "awaiting decision" marker.
            clear_labels(mail, email_id, [LABEL_FLAGGED])


def add_flag(email_id):
    """Mark a message as 'presented, awaiting decision' (race-safe pre-label)."""
    with connect_gmail() as mail:
        ensure_labels(mail)
        set_labels(mail, email_id, [LABEL_FLAGGED])


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


def _to_job(uid, msg):
    """Build a job dict from a message + its UID."""
    return {
        "id": uid,
        "subject": _decode(msg.get("Subject")) or "(no subject)",
        "from": _decode(msg.get("From")),
        "body": _clean_body(msg),
        "date": msg.get("Date", ""),
    }


def _fetch_uids(mail, search_parts):
    """Run a UID SEARCH and return (uids, error)."""
    status, messages = mail.uid("SEARCH", None, *search_parts)
    if status != "OK":
        return [], "Search failed"
    return messages[0].split(), "Success"


def _fetch_job(mail, uid):
    """Fetch one message by UID, return job dict or None."""
    status, data = mail.uid("FETCH", uid, "(BODY.PEEK[] UID)")
    if status != "OK":
        return None
    uid_str = uid.decode(errors="ignore")
    for part in data:
        if isinstance(part, tuple):
            hdr = part[0]
            m = _UID_RE.search(hdr)
            if m:
                uid_str = m.group(1).decode()
            return _to_job(uid_str, email.message_from_bytes(part[1]))
    return None


def fetch_new_emails(limit=10, job_filter=True):
    """Fetch UNSEEN emails not already labeled Bot/Processed.

    Returns (new_emails, error). id is the message UID, used with UID STORE to
    apply labels later.
    """
    gmail_user = get_key("GMAIL_ADDRESS")
    if not gmail_user or not get_key("GMAIL_APP_PASSWORD"):
        return [], "Gmail credentials missing"

    new_emails = []
    try:
        with connect_gmail() as mail:
            ensure_labels(mail)
            uids, err = _fetch_uids(mail, ["UNSEEN", "NOT", "X-GM-LABELS", f'"{LABEL_PROCESSED}"'])
            if err != "Success":
                return [], "No unread emails" if not uids else err
            for uid in uids[-limit:][::-1]:  # newest first
                job = _fetch_job(mail, uid)
                if job and (not job_filter or _is_job_email(job["subject"], job["body"])):
                    new_emails.append(job)
    except Exception as e:
        return [], f"IMAP Error: {e}"

    return new_emails, "Success"


def list_flagged():
    """Return job dicts for messages labeled Bot/Flagged (awaiting decision).

    Used on startup to re-present undecided jobs after a restart. Answering a
    job (approve/skip) clears Bot/Flagged, so handled jobs are naturally absent.
    """
    jobs = []
    try:
        with connect_gmail() as mail:
            ensure_labels(mail)
            uids, _ = _fetch_uids(mail, ["X-GM-LABELS", f'"{LABEL_FLAGGED}"'])
            for uid in uids:
                job = _fetch_job(mail, uid)
                if job:
                    jobs.append(job)
    except Exception:
        pass
    return jobs