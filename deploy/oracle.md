# Deploy on Oracle Cloud Always Free (VM)

Real VM — full network egress (Gmail SMTP works), persistent disk, no idle-sleep.
Recommended over Render free (which blocks SMTP 465/587).

## 1. Create an Always Free VM
Oracle Cloud Console → Compute → Instances → Create instance.
- Image: **Ubuntu 24.04** (amd64 or ARM — either works).
- Shape: an **Always Free** tier eligible shape (e.g. `VM.Standard.E2.1.Micro` / `VM.Standard.A1.Flex`).
- Add your SSH public key to access the instance.
- Note: Oracle free tiers are region/availability limited; if shapes show unavailable, pick another region.

## 2. SSH in + install deps
```sh
ssh -i ~/.ssh/your_key ubuntu@<vm_public_ip>

sudo apt update && sudo apt install -y python3-venv python3-pip git curl
sudo apt install -y libreoffice            # optional: enables PDF attachments
```

## 3. Clone + deps
```sh
sudo mkdir -p /opt/job-agent && cd /opt/job-agent
sudo git clone https://github.com/ferdousbhuiya/job-agent.git .
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt
```

## 4. Secrets (never commit these)
```sh
sudo mkdir -p /etc/job-agent
sudo nano /etc/job-agent/.env
```
Contents (copy from `.env.example`):
```
GMAIL_ADDRESS=your@gmail.com
GMAIL_APP_PASSWORD=your-16-char-app-password
GROQ_API_KEY=gsk_...
TELEGRAM_BOT_TOKEN=123456:ABC-...
TELEGRAM_CHAT_ID=123456789
YOUR_NAME=Your Name
POLL_INTERVAL_MIN=15
```

## 5. systemd service (auto-start, auto-restart, survives reboot)
`/etc/systemd/system/job-agent.service`:
```ini
[Unit]
Description=AI Job Agent Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/job-agent
EnvironmentFile=/etc/job-agent/.env
ExecStart=/opt/job-agent/venv/bin/python /opt/job-agent/runner.py --interval 0
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```
Install + start:
```sh
sudo systemctl daemon-reload
sudo systemctl enable --now job-agent
```

## 6. Verify
```sh
sudo systemctl status job-agent
journalctl -u job-agent -f          # live logs
```
Expected: `Bot started. ctrl+C to stop.` then `Poll loop started, interval=15 min`.

Confirm Gmail SMTP reachable from the VM:
```sh
python3 -c "import smtplib; s=smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=10); s.login('$GMAIL_ADDRESS','$GMAIL_APP_PASSWORD'); print('SMTP OK')"
```

Then in Telegram: `/scan` → tap ✅ on a job → application email sends via Gmail SMTP.

## Notes
- Dedupe is server-side (Gmail labels) — nothing on disk is critical; restart is safe.
- Groq quota is account-level (not host-level): the 100k/day token limit applies here too.
- To update after a `git push`: `cd /opt/job-agent && sudo git pull && sudo systemctl restart job-agent`.