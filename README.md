# Hitek Upload / Extract Bot (Ubuntu / Docker / RDP)

A Telegram bot for receiving large split archives such as `hitek.7z.001 ... hitek.7z.057`, queuing every forwarded document, and downloading **one file at a time**. It reports live percentage, speed, ETA, queue state, archive validation, extraction progress, disk usage, and errors back to Telegram.

## Important security note

Never commit `.env` to GitHub. If a bot token or Telegram API hash has been pasted publicly, rotate it before deployment.

## Install on Ubuntu/container

```bash
unzip hitek_upload_bot_fixed.zip
cd hitek_upload_bot
sudo bash install.sh
nano /opt/hitek-bot/.env
```

Fill the real values:

```env
BOT_TOKEN=...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
ADMIN_IDS=8484531901
BASE_DIR=/opt/hitek-bot
UPLOAD_DIR=/opt/hitek-bot/uploads
EXTRACT_DIR=/opt/hitek-bot/extracted
STATE_DB=/opt/hitek-bot/queue.db
SESSION_DIR=/opt/hitek-bot/session
```

`TELEGRAM_API_ID` and `TELEGRAM_API_HASH` must be a matching pair from `my.telegram.org/apps`.

### Start in a Docker/RDP/container without systemd

```bash
/opt/hitek-bot/start.sh
```

View the bot console:

```bash
screen -r hitekbot
```

Detach without stopping it: `Ctrl+A`, then `D`.

Stop:

```bash
/opt/hitek-bot/stop.sh
```

If startup fails, run it in foreground to see the exact traceback:

```bash
cd /opt/hitek-bot
venv/bin/python3 bot.py
```

### On a normal systemd Ubuntu VM

```bash
systemctl enable --now hitek-upload-bot
journalctl -u hitek-upload-bot -f
```

## First diagnostics

Open the bot in a **private Telegram chat**, press Start, then send:

```text
/whoami
/ping
/start
```

`/whoami` works even when your ID is not in `ADMIN_IDS`; it tells you the exact Telegram numeric user ID and whether it is allowed. Every incoming update is also logged in the console as `INCOMING ...`, making silent-handler issues easy to diagnose.

At startup the bot also tries to send each configured admin a health message. Telegram only allows that after the user has opened/started the bot at least once.

## Commands

- `/start` or `/help` — command list
- `/ping` — handler health check
- `/whoami` — show user/chat ID and admin status
- Send/forward documents — queue automatically
- `/queue` — current + waiting + done/failed counts
- `/files` — stored file summary
- `/parts` — validate `.7z.001 ...` sequence and list missing parts
- `/disk` — disk/RAM usage
- `/du` — upload/extracted directory sizes
- `/test7z` — `7z t` on a complete sequence
- `/info7z` — archive metadata/listing
- `/extract7z CONFIRM` — extract into `EXTRACT_DIR` with live progress
- `/retryfailed` — retry failed Telegram downloads
- `/clearqueue CONFIRM` — clear waiting jobs
- `/deleteuploads CONFIRM` — delete received archive files
- `/deleteextracted CONFIRM` — clear extraction directory

## Queue behavior

You can forward all archive parts rapidly. Telegram messages become tiny persistent queue records in `queue.db`; only **one document is downloaded at a time**. If the bot restarts, interrupted downloads are put back in the queue.

Download progress is edited roughly every three seconds with current bytes, percentage, transfer speed, ETA, overall job number, completed count, and waiting count.

## GitHub update workflow

After pulling a new version:

```bash
cd /path/to/repo
sudo bash install.sh
/opt/hitek-bot/stop.sh
/opt/hitek-bot/start.sh
```

The installer intentionally **does not overwrite** an existing `/opt/hitek-bot/.env`.

## Troubleshooting

If the bot says `API_ID_INVALID`, the `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` pair is wrong. Use a matching pair from `my.telegram.org/apps`.

If the bot starts but seems silent, attach to the screen and send `/whoami`:

```bash
screen -r hitekbot
```

You should see an `INCOMING` log line for every received message. If there is no `INCOMING` line, verify you are messaging the exact username printed in `Started as @...`, then remove only the bot session and restart:

```bash
/opt/hitek-bot/stop.sh
rm -f /opt/hitek-bot/session/hitek_upload_bot.session*
/opt/hitek-bot/start.sh
```
