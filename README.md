# Hitek Upload / Extract Bot for Ubuntu

Purpose: forward many large `.7z.001`, `.7z.002`, ... parts to a Telegram bot. The bot queues every document and downloads **one at a time**, so dozens of forwarded files do not download concurrently.

## Why Pyrogram / MTProto?
The normal hosted Bot API `getFile` endpoint is limited for downloads. This project uses Pyrogram/PyroFork with Telegram API ID + API hash + bot token, matching the approach in your older manager bot and avoiding the hosted Bot API download path.

## Install

```bash
unzip hitek_upload_bot.zip
cd hitek_upload_bot
sudo bash install.sh
sudo nano /opt/hitek-bot/.env
sudo systemctl enable --now hitek-upload-bot
sudo journalctl -u hitek-upload-bot -f
```

Fill `.env` with:
- `BOT_TOKEN` from BotFather
- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from my.telegram.org/apps
- `ADMIN_IDS` = your Telegram numeric user ID

## Bot commands

- Send/forward documents: automatically queued
- `/queue` current + waiting + completed/failed counts
- `/files` list saved files
- `/parts` validate `.7z.001` sequence and show missing volumes
- `/disk` disk + basic RAM info
- `/du` uploads/extracted sizes
- `/test7z` run `7z t` after all parts are present
- `/info7z` show 7z metadata/listing
- `/extract7z CONFIRM` extract to `/opt/hitek-bot/extracted`
- `/retryfailed` retry failed Telegram downloads
- `/clearqueue CONFIRM` remove waiting jobs only
- `/deleteuploads CONFIRM` delete received parts
- `/deleteextracted CONFIRM` clear extraction folder

## Queue behavior

Forwarding 57 files rapidly is OK. Each message is first stored as a small queue record. Only one file is downloaded at any time. Queue metadata is persisted in `/opt/hitek-bot/queue.db`, so after a service restart, waiting downloads are requeued.

## Security

Only IDs in `ADMIN_IDS` can use the bot. There is deliberately no arbitrary `/shell` or `/cmd` endpoint. A leaked bot token should not become unrestricted remote shell access to the Ubuntu server.

Keep `.env` permissions restricted:

```bash
chmod 600 /opt/hitek-bot/.env
```

## Useful Ubuntu commands

```bash
systemctl status hitek-upload-bot
journalctl -u hitek-upload-bot -f
systemctl restart hitek-upload-bot
systemctl stop hitek-upload-bot

df -h /
du -sh /opt/hitek-bot/uploads /opt/hitek-bot/extracted
```

## Important

Do not start `/extract7z CONFIRM` until `/parts` reports a complete contiguous sequence. For your current archive this should look like `.001` through `.057` with no missing numbers.

## Live progress
Downloads update one Telegram status message about every 3 seconds with current bytes, percentage, transfer speed, ETA, overall job number, completed count, and waiting count. Archive extraction uses 7-Zip progress output and updates about every 4 seconds with extraction percentage (when reported) and remaining disk space.

For large batches, send/forward all documents to the bot. They are queued and downloaded strictly one at a time.
