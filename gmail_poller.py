# gmail_poller.py
# Polls Gmail for new job-related emails. Deduplication is handled server-side
# with Gmail labels (Bot/Processed), so it survives container restarts where the
# local filesystem is ephemeral (e.g. Render free worker). Returns only NEW
# (unprocessed) emails.
import re
import imaplib
import email
import datetime
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
                "role", "recruit", "apply", "opening", "career", "talent",
                "engineer", "manager", "developer", "designer", "analyst",
                "consultant", "architect", "scientist")

# Subject lines that look job-ish ("opporunity" etc.) but are really platform
# alerts / notifications / forms, not a concrete role. Matched on the subject.
_SUBJECT_BLOCK = ("new form submission", "form submission", "notifications",
                  "notification", "welcome to", "welcome from", "account",
                  "your profile", "profile might")

# Strong signals this is NOT a real job posting (promos, deals, alerts, spam).
# Any hit overrides a weak JOB_KEYWORDS match. Match on word boundaries to avoid
# a promo subject like "Career day" falsely slipping through.
_PROMO_KEYWORDS = (
    "promotion", "promo", "discount", "deal", "sale", "offer", "% off", "voucher",
    "coupon", "flash sale", "free shipping", "save ", "order now", "don't miss",
    "unsubscribe", "weekly", "newsletter", "subscription", "reward", "cashback",
    "price drop", "big savings", "clearance", "of sale", "update your app",
    "notification", "your order", "shipping now", "package", "track your",
    "thank you for your order", "member", "loyalty", "points ", "wallet",
    "instagram", "facebook", "tiktok", "reels", "feed", "post", "follow us",
)
# Senders never to treat as a job. Lowercased, case-insensitive match against
# the sender name+domain.
# Senders never to treat as a job. Lowercased, case-insensitive match against
# the sender name+domain. Only clearly non-recruiting brands go here — job
# platforms legitimately send from noreply@, so that prefix is NOT a blocker.
_BLOCKED_SENDERS = (
    "temu", "amazon", "ebay", "alibaba", "ali express", "wiish", "shein",
    "walmart", "shopify", "best buy", "target", "wish", "notify", "paypal",
    "newsletter", "alert@", "kaggle", "supabase", "twilio", "redfin",
    "real estate", "preapproval", "mortgage", "zillow", "redfin",
    "guidanceresidential", "insurance", "loan", "property",
    "form submission", "striive", "careerjet", "job alert",
    "your daily jobs",
)

_UID_RE = re.compile(rb"UID (\d+)")


@contextmanager
def connect_gmail():
    """Yield a logged-in, INBOX-selected IMAP connection; always closes it."""
    # Timeout on the SSL socket so a stalled Gmail connection (or a Gmail
    # conn-limit deadlock) fails fast instead of hanging /scan forever.
    mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=20)
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


def _is_job_email(subject, body, sender=""):
    """True if this email looks like a real job posting, not promo/spam.

    Checks:
      1. BLOCKED sender (promo domains/Google forms etc.) -> always False.
      2. JOB title is in the SUBJECT line: strong recruiter signal, accept
         regardless of body wording (bodies of legit recruiters routinely
         contain promo-ish words like "notification"/"state"/"available" that a
         naive body veto would wrongly suppress).
      3. Otherwise, if a job keyword appears only in the body, a strong PROMO
         hit (sale, discount, unsubscribe, etc.) wins and vetoes it.
    """
    if sender and any(b in sender.lower() for b in _BLOCKED_SENDERS):
        return False

    subj = subject.lower()
    body_l = body.lower()

    # Platform alert/notification subjects that would otherwise keyword-match.
    if any(b in subj for b in _SUBJECT_BLOCK):
        return False

    if any(k in subj for k in JOB_KEYWORDS):
        return True

    has_job = any(k in body_l for k in JOB_KEYWORDS)
    if not has_job:
        return False
    if any(p in body_l for p in _PROMO_KEYWORDS):
        return False
    return True


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


def fetch_new_emails(limit=40, job_filter=True):
    """Fetch job-relevant emails from a recent time window, not yet Processed.

    Unlike the older `UNSEEN`-only scan, this catches mail the user already read
    (Gmail marks read on preview/phone). Search is `SINCE N-day window` AND NOT
    labeled Bot/Processed, newest first. N comes from SCAN_DAYS (default 3);
    `limit` caps how many messages get fetched in one scan.

    Returns (new_emails, error). id is the message UID, used with UID STORE to
    apply labels later.
    """
    gmail_user = get_key("GMAIL_ADDRESS")
    if not gmail_user or not get_key("GMAIL_APP_PASSWORD"):
        return [], "Gmail credentials missing"

    days = int(get_key("SCAN_DAYS") or 10)
    since = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%d-%b-%Y")

    new_emails = []
    try:
        with connect_gmail() as mail:
            ensure_labels(mail)
            uids, err = _fetch_uids(mail, ["SINCE", since, "NOT", "X-GM-LABELS", f'"{LABEL_PROCESSED}"'])
            if err != "Success":
                return [], "Search failed"
            for uid in uids[-limit:][::-1]:  # newest first
                job = _fetch_job(mail, uid)
                if job and (not job_filter or _is_job_email(job["subject"], job["body"], job["from"])):
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