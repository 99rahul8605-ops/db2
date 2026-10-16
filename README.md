# Hitek Upload Bot (Telethon, Safe Upload Mode)

Simple Ubuntu/container bot for large Telegram files and split 7z archives.

## Safety behavior
Upload mode is **OFF by default on every restart**.

- `/upload_on` — allow files to be queued/downloaded
- `/upload_off` — stop accepting new files
- `/upload_status` — show current state

If a file is sent while upload mode is OFF, it is ignored and not downloaded.
Files already queued before `/upload_off` continue normally.

## Main commands
- `/start`
- `/ping`
- `/whoami`
- `/upload_on`
- `/upload_off`
- `/upload_status`
- `/queue`
- `/files`
- `/parts`
- `/disk`
- `/test7z`
- `/info7z`
- `/extract7z CONFIRM`

## Setup
```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp env.example .env
nano .env
venv/bin/python3 bot.py
```

## Screen
```bash
screen -dmS hitekbot bash -lc 'cd /root/db2 && ./start.sh'
screen -r hitekbot
```
