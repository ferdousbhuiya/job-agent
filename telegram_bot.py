# telegram_bot.py
# Telegram control center. Receives new jobs from the poller, shows JD + tailed
# resume + cover, and asks the user to Approve (send application) or Skip.
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters)

from config import get_key
from gmail_poller import fetch_new_emails, mark_presented, add_flag, list_flagged
from ai_tailor import extract_text_from_docx, build_application_from_jd
from sender import build_application_files, send_application

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Pending jobs awaiting user decision: callback_key -> dict
PENDING = {}

APP_NAME = get_key("YOUR_NAME") or "Applicant"
EMAIL = get_key("GMAIL_ADDRESS")
MASTER_RESUME = "master_resume.docx"


def only_auth(update: Update) -> bool:
    owner = get_key("TELEGRAM_CHAT_ID")
    if not owner:
        return True
    return str(update.effective_user.id) == owner

# ---- helpers ----
def job_key(job):
    return f"{EMAIL}:{job['id']}"


def resolved_chat_id(chat_id):
    """Bot must always send somewhere. Default to configured owner if no explicit chat."""
    return chat_id or get_key("TELEGRAM_CHAT_ID")


def format_job_summary(job, app):
    return (
        f"*New Job Alert* 📬\n"
        f"Role: *{app['job_title'] or 'unknown'}* at *{app['company'] or '?'}*\n"
        f"Subject: `{job['subject']}`\n"
        f"From:    {job['from']}\n"
        f"\n**Job Description:**\n{job['body'][:1500]}"
    )


# ---- sync -> async bridges (IMAP work must not run on the event loop) ----
async def poll_new_jobs():
    return await asyncio.to_thread(fetch_new_emails)


async def mark_presented_async(*args, **kwargs):
    return await asyncio.to_thread(mark_presented, *args, **kwargs)


async def add_flag_async(email_id):
    return await asyncio.to_thread(add_flag, email_id)


async def list_flagged_async():
    return await asyncio.to_thread(list_flagged)


# ---- bot handlers ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not only_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text(
        "🤖 *AI Job Agent*\n\n"
        "I scan your Gmail for job postings, tailor your resume + cover letter, "
        "and wait for your approval before sending the application.\n\n"
        "Commands:\n`/scan` — check Gmail now\n`/status` — show pending\n"
        "`/help` — this message"
    )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not only_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text("Scanning Gmail...")
    await process_new_jobs(update.effective_chat.id, context)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not only_auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    pending = list(PENDING.keys())
    if not pending:
        await update.message.reply_text("No jobs pending decision.")
    else:
        await update.message.reply_text(f"Pending jobs: {len(pending)}")


# ---- core: fetch + tailor + present ----
async def process_new_jobs(chat_id, context):
    """Fetch unread Gmail, tailor each new job, present to user for decision.

    chat_id may be None (background poll) -> falls back to configured owner.
    """
    target = resolved_chat_id(chat_id)
    send = context.bot.send_message
    if not target:
        return  # nobody to notify

    new_jobs, err = await poll_new_jobs()
    if err != "Success":
        await send(target, f"⚠️ {err}")
        return
    if not new_jobs:
        if chat_id:
            await send(target, "No new job emails.")
        return

    try:
        master_text = extract_text_from_docx(MASTER_RESUME)
    except FileNotFoundError:
        await send(target, f"❌ {MASTER_RESUME} not found. Add it next to the script.")
        return

    for job in new_jobs:
        key = job_key(job)
        try:
            app = build_application_from_jd(master_text, job["body"])
        except Exception as e:
            logger.exception("tailoring failed")
            await send(target, f"⚠️ AI tailoring failed for {job['subject']}: {e}")
            continue

        try:
            resume_pdf, cover_pdf, resume_docx, cover_docx = await asyncio.to_thread(
                build_application_files, app["resume"], app["cover_letter"], job["id"])
        except Exception as e:
            logger.exception("doc build failed")
            await send(target, f"⚠️ Could not build documents for {job['subject']}: {e}")
            continue

        # Persist "awaiting decision" flag before presenting, so a restart can
        # re-present this job via list_flagged().
        await add_flag_async(job["id"])

        PENDING[key] = {"job": job, "app": app,
                        "resume_pdf": resume_pdf, "cover_pdf": cover_pdf,
                        "recipient": app["recipient"]}

        # Mark processed (tombstone) + keep the flag so it survives a restart.
        await mark_presented_async(job["id"], job["subject"], job["from"], flag=True)

        await send(target, format_job_summary(job, app), parse_mode="Markdown")
        await send(target,
                   f"*Tailored Resume*\n{app['resume'][:1800]}",
                   parse_mode="Markdown")
        await send(target,
                   f"*Tailored Cover Letter*\n{app['cover_letter'][:1800]}",
                   parse_mode="Markdown")

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Send application", callback_data=f"send:{key}"),
            InlineKeyboardButton("❌ Skip", callback_data=f"skip:{key}"),
        ]])
        await send(target, "Ready to send?", reply_markup=keyboard)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    # A stale/old query (button tapped long after its message was sent, e.g. from a
    # re-presented copy) can make answer() raise BadRequest. Never let that crash
    # the handler — log and continue on to the guarded PENDING lookup.
    try:
        await query.answer()
    except Exception:
        logger.warning("callback query.answer failed (stale query?)", exc_info=True)
    if not only_auth(update):
        await query.message.reply_text("Unauthorized.")
        return

    data = query.data.split(":", 1)  # action:key
    if len(data) != 2:
        return
    action, key = data
    entry = PENDING.get(key)
    if not entry:
        await query.message.reply_text("This job was already handled or expired. Run /scan again.")
        return

    if action == "skip":
        PENDING.pop(key, None)
        await mark_presented_async(entry["job"]["id"], entry["job"]["subject"],
                                   entry["job"]["from"], sent=False)
        await query.message.reply_text("Skipped. Application not sent.")
        return

    # ---- send ----
    recipient = entry.get("recipient")
    if not recipient:
        # No recipient recovered from JD -> ask the owner to text one in chat.
        context.user_data["awaiting_recipient_for"] = key
        PENDING[key] = entry
        await query.message.reply_text(
            "No recipient email found in the JD.\n"
            "Send the application email address as a plain text message to set it, "
            "or /skip to discard.")
        return

    try:
        app = entry["app"]
        subject = f"Application for {app['job_title'] or 'the role'} — {APP_NAME}"
        body = (f"Dear Hiring Team,\n\n{app['cover_letter']}\n\n"
                f"Best regards,\n{APP_NAME}\n{EMAIL}")
        # Send in a worker thread so a slow/stalled SMTP connect never blocks
        # the Telegram event loop (getUpdates / callbacks would freeze).
        await asyncio.to_thread(send_application, recipient, subject, body,
                                entry["resume_pdf"], entry["cover_pdf"])
        PENDING.pop(key, None)
        await mark_presented_async(entry["job"]["id"], entry["job"]["subject"],
                                   entry["job"]["from"], sent=True)
        await query.message.reply_text(f"✅ Application sent to {recipient}.")
    except Exception as e:
        logger.exception("send failed")
        await query.message.reply_text(f"❌ Failed to send: {e}. Run /scan to retry this job.")


async def manual_recipient(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Raw text handler: captures a recipient email the user typed for a pending job."""
    if not only_auth(update):
        return
    key = context.user_data.get("awaiting_recipient_for")
    if not key:
        return  # not expecting text
    email = update.message.text.strip()
    if "@" not in email or "." not in email:
        await update.message.reply_text("That doesn't look like an email. Try again, or /skip.")
        return
    entry = PENDING.get(key)
    if entry:
        entry["recipient"] = email
        await mark_presented_async(entry["job"]["id"], entry["job"]["subject"],
                                   entry["job"]["from"], flag=True)
        await update.message.reply_text(f"Recipient set to {email}. Now tap ✅ Send application again.")
    context.user_data.pop("awaiting_recipient_for", None)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not only_auth(update):
        return
    await update.message.reply_text(
        "Commands:\n/start\n/scan\n/status\n/help\n\n"
        "After each job, tap ✅ Send or ❌ Skip.")


def build_bot():
    token = get_key("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manual_recipient))
    return app


# ---- background poller wired into the bot ----
async def poll_loop(application, interval_minutes):
    """Poll Gmail on a schedule; send any new jobs to the owner's chat."""
    logger.info("Poll loop started, interval=%s min", interval_minutes)
    while True:
        try:
            await process_new_jobs(None, application)
        except Exception:
            logger.exception("poll loop iteration failed")
        await asyncio.sleep(interval_minutes * 60)


async def rehydrate(context, chat_id):
    """Re-present any job flagged as awaiting a decision after a restart.

    Tailored docs were lost with the ephemeral filesystem, so rebuild them from
    the stored message body. Already-processed/sent jobs are excluded by the
    label search.
    """
    target = resolved_chat_id(chat_id)
    if not target:
        return
    send = context.bot.send_message
    try:
        jobs = await list_flagged_async()
    except Exception:
        logger.exception("rehydrate: listing flagged jobs failed")
        return
    if not jobs:
        return

    try:
        master_text = extract_text_from_docx(MASTER_RESUME)
    except FileNotFoundError:
        await send(target, f"❌ {MASTER_RESUME} not found. Add it next to the script.")
        return

    logger.info("Rehydrating %s pending job(s) from Gmail label", len(jobs))
    for job in jobs:
        key = job_key(job)
        try:
            app = build_application_from_jd(master_text, job["body"])
            resume_pdf, cover_pdf, _, _ = await asyncio.to_thread(
                build_application_files, app["resume"], app["cover_letter"], job["id"])
        except Exception as e:
            logger.exception("rehydrate doc build failed")
            await send(target, f"⚠️ Could not re-present {job['subject']}: {e}")
            continue

        PENDING[key] = {"job": job, "app": app,
                        "resume_pdf": resume_pdf, "cover_pdf": cover_pdf,
                        "recipient": app["recipient"]}
        await send(target, format_job_summary(job, app), parse_mode="Markdown")
        await send(target, f"**Tailored Resume**\n{app['resume'][:1800]}", parse_mode="Markdown")
        await send(target, f"**Tailored Cover Letter**\n{app['cover_letter'][:1800]}",
                   parse_mode="Markdown")
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Send application", callback_data=f"send:{key}"),
            InlineKeyboardButton("❌ Skip", callback_data=f"skip:{key}"),
        ]])
        await send(target, "Ready to send?", reply_markup=keyboard)


async def run_bot(interval_minutes):
    app = build_bot()
    async with app:
        await app.start()
        await app.updater.start_polling()
        logger.info("Bot started. ctrl+C to stop.")
        try:
            await rehydrate(app, resolved_chat_id(None))
        except Exception:
            logger.exception("rehydrate failed")
        await poll_loop(app, interval_minutes)