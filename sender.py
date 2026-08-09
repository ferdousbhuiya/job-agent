# sender.py
# Builds resume + cover letter as .docx, converts to PDF via LibreOffice,
# then sends the application email to the extracted recipient via Resend API.
# (Gmail SMTP is blocked on Render free workers, so mail goes through Resend —
# a REST API on port 443 that Render allows.)
import os
import shutil
import subprocess
import base64
from docx import Document
import httpx
from config import get_key


def text_to_docx(text, path):
    doc = Document()
    for line in text.split("\n"):
        if line.strip():
            doc.add_paragraph(line.strip())
    doc.save(path)
    return path


def find_soffice():
    """Return soffice executable path, or None."""
    env = get_key("LIBREOFFICE_PATH")
    if env and os.path.exists(env):
        return env
    candidates = [
        "soffice", "libreoffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice", "/usr/bin/libreoffice",
    ]
    for c in candidates:
        if shutil.which(c):
            return c
    return None


def docx_to_pdf(docx_path, out_dir):
    """Convert one .docx to .pdf using LibreOffice headless. Returns pdf path."""
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) not found. Install it or set LIBREOFFICE_PATH.")

    out_dir = os.path.abspath(out_dir)
    proc = subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir, os.path.abspath(docx_path)],
        capture_output=True, text=True, timeout=120,
    )
    base = os.path.splitext(os.path.basename(docx_path))[0]
    pdf_path = os.path.join(out_dir, base + ".pdf")
    if not os.path.exists(pdf_path):
        raise RuntimeError(f"LibreOffice conversion failed: {proc.stdout} {proc.stderr}")
    return pdf_path


def build_application_files(resume_text, cover_text, job_id, work_dir="work"):
    """Write resume.docx/cover.docx; convert to PDF only if LibreOffice is present.

    Returns (attach_a, attach_b, resume_docx, cover_docx) where each attach is a
    path — either a .pdf (soffice available) or the .docx (fallback). Never raises
    just because LibreOffice is missing.
    """
    os.makedirs(work_dir, exist_ok=True)
    resume_docx = os.path.join(work_dir, f"resume_{job_id}.docx")
    cover_docx = os.path.join(work_dir, f"cover_{job_id}.docx")
    text_to_docx(resume_text, resume_docx)
    text_to_docx(cover_text, cover_docx)

    if find_soffice():
        try:
            resume_attach = docx_to_pdf(resume_docx, work_dir)
            cover_attach = docx_to_pdf(cover_docx, work_dir)
        except Exception:
            # PDF failed despite soffice present — fall back to DOCX rather than fail.
            resume_attach, cover_attach = resume_docx, cover_docx
    else:
        # No LibreOffice -> attach the .docx files directly.
        resume_attach, cover_attach = resume_docx, cover_docx

    return resume_attach, cover_attach, resume_docx, cover_docx


def _mime(path):
    return ("application", "pdf") if path.lower().endswith(".pdf") else (
        "application", "vnd.openxmlformats-officedocument.wordprocessingml.document")


def send_application(to_email, subject, body, resume_attach, cover_attach):
    """Send application email via the Resend API (port 443, allowed on Render).

    Requires RESEND_API_KEY. Attachments are sent as base64 payloads. Sending is
    synchronous; callers should run it in a worker thread (see telegram_bot).
    """
    api_key = get_key("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY missing")

    sender_email = get_key("RESEND_FROM") or get_key("GMAIL_ADDRESS")
    # Replies go to the applicant's real inbox, not the branded Resend address —
    # otherwise recruiter replies land nowhere. Override with RESEND_REPLY_TO.
    reply_to = get_key("RESEND_REPLY_TO") or get_key("GMAIL_ADDRESS")

    attachments = []
    for path in (resume_attach, cover_attach):
        if not path or not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            attachments.append({
                "filename": os.path.basename(path),
                "content": base64.b64encode(f.read()).decode(),
                "content_type": _mime(path)[0] + "/" + _mime(path)[1],
            })

    payload = {
        "from": sender_email,
        "to": [to_email],
        "reply_to": reply_to,
        "subject": subject,
        "text": body,
        "attachments": attachments,
    }

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
    except Exception as e:
        raise RuntimeError(f"Resend request failed: {e}")
    if resp.status_code != 200:
        raise RuntimeError(f"Resend API error {resp.status_code}: {resp.text[:300]}")
    return True