# Hitek Pyrogram Upload Bot

Fast large-file Telegram downloader for Ubuntu/container servers using **PyroFork (Pyrogram fork) + TgCrypto**.

## Main behavior

- Upload mode starts **OFF** after every bot restart.
- `/upload_on` enables accepting documents.
- `/upload_off` blocks accidental new files.
- Forward files in any order; filenames are preserved.
- Files are persisted in SQLite queue and downloaded **one at a time**.
- Interrupted `downloading` jobs are put back into queue on restart.
- Live per-file percentage, speed and ETA (about every 3 seconds).
- Downloads use a `.downloading` temp file and atomic rename only after size verification.
- Already-complete files with the same expected size are skipped.
- Supports split archives named `hitek.7z.001`, `.002`, etc.
- 7-Zip test/info/extraction commands included.
- No unrestricted shell command is exposed through Telegram.

## Ubuntu prerequisites

```bash
apt-get update
apt-get install -y python3 python3-venv p7zip-full screen
```

## First setup

```bash
cd /root/db2
cp .env.example .env
nano .env
./start.sh
```

For background screen:

```bash
./run_screen.sh
```

Attach:

```bash
screen -r hitekbot
```

Stop:

```bash
./stop.sh
```

## After GitHub updates

```bash
cd /root/db2
git pull
./stop.sh
./run_screen.sh
```

`start.sh` automatically installs any changed Python requirements.

## Commands

- `/start` or `/help`
- `/ping`
- `/whoami`
- `/upload_on`
- `/upload_off`
- `/upload_status`
- `/queue`
- `/retryfailed`
- `/cancelcurrent CONFIRM`
- `/files`
- `/parts`
- `/disk`
- `/test7z`
- `/info7z`
- `/extract7z CONFIRM`

## Recommended upload sequence

1. `/upload_on`
2. Forward all `.7z.001 ... .057` parts (any order)
3. `/upload_off`
4. Wait until `/queue` shows no queued/downloading jobs
5. `/parts`
6. `/test7z`
7. `/extract7z CONFIRM`

## Credentials

`TELEGRAM_API_ID` and `TELEGRAM_API_HASH` come from `my.telegram.org/apps`, not BotFather. `BOT_TOKEN` comes from BotFather.

Never commit `.env` to GitHub.
