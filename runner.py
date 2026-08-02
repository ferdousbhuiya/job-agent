# runner.py — Main entrypoint. Starts the Telegram bot + background Gmail polling loop.
import argparse
import asyncio
import logging
import os
import sys

from config import get_key
from telegram_bot import run_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LOCK_FILE = os.path.join(os.path.dirname(__file__), "runner.lock")
_LOCK_HANDLE = None


def _acquire_lock():
    """Single-instance guard via a real OS file lock (portable).

    Uses `fcntl` on POSIX (Linux cloud hosts) and `msvcrt` on Windows. The lock is
    held on the file itself, so a second process cannot slip through the
    empty-file race; it is released automatically when the process exits/dies.
    """
    global _LOCK_HANDLE
    import platform
    try:
        if platform.system() == "Windows":
            import msvcrt
            handle = open(LOCK_FILE, "ab+")
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            handle = open(LOCK_FILE, "ab")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            handle.close()
        except UnboundLocalError:
            pass
        try:
            with open(LOCK_FILE, "rb") as f:
                pid = f.read().decode(errors="ignore").strip() or "?"
        except OSError:
            pid = "?"
        print(f"Another bot instance is already running (pid {pid} holds lock). "
              f"Stop it, or delete runner.lock, then retry.", file=sys.stderr)
        sys.exit(1)
    # Hold the lock: record our pid for diagnostics.
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()).encode())
    handle.flush()
    handle.seek(0)  # reset so later readers see the pid from byte 0
    _LOCK_HANDLE = handle
    return os.getpid()


def main():
    _acquire_lock()
    parser = argparse.ArgumentParser(description="Start AI Job Application Agent")
    parser.add_argument("--interval", type=int, default=0,
                        help="Poll Gmail every N minutes. 0 = no auto-poll (use /scan).")
    args = parser.parse_args()

    interval = args.interval or int(get_key("POLL_INTERVAL_MIN") or 15)
    logger.info("Starting AI Job Agent, poll interval=%s minutes", interval)
    asyncio.run(run_bot(interval))
    # On exit, the OS releases the file lock; global handle keeps it alive while running.


if __name__ == "__main__":
    main()