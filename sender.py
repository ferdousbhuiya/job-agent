# sender.py
# Builds resume + cover letter as .docx, converts to PDF via LibreOffice,
# then sends the application email to the extracted recipient via SMTP.
import os
import shutil
import subprocess
from docx import Document
from email.message import EmailMessage
import smtplib
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
    """Send application email via Gmail SMTP with resume + cover attached."""
    gmail_user = get_key("GMAIL_ADDRESS")
    password = get_key("GMAIL_APP_PASSWORD")

    if not gmail_user or not password:
        raise RuntimeError("Gmail credentials missing")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = to_email
    msg.set_content(body)

    for path in (resume_attach, cover_attach):
        if not path or not os.path.exists(path):
            continue
        maintype, subtype = _mime(path)
        with open(path, "rb") as f:
            msg.add_attachment(
                f.read(), maintype=maintype, subtype=subtype,
                filename=os.path.basename(path),
            )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, password)
        smtp.send_message(msg)
    return True