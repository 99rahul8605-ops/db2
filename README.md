# Hitek Pyrogram Parallel Upload Bot

Fast Telegram -> Ubuntu downloader for large split archive parts using **PyroFork + TgCrypto-pyrofork**.

## What this build does

- Upload mode starts **OFF** on every bot restart.
- `/upload_on` is required before documents are accepted.
- Forward parts in any order; filenames are preserved.
- The queue processes **ONE file at a time**.
- The one active file is downloaded through **4 parallel Telegram ranges** by default.
- Live combined percentage, speed and ETA.
- Partial range files are kept on disk after a crash, restart, cancellation or failed transfer.
- Resume continues from completed **1 MiB chunks** instead of restarting the full file.
- The final file is only renamed into place after all ranges are complete and the final size matches.
- `.7z.001`, `.002`, ... integrity/order check and extraction commands are included.
- No unrestricted shell command is exposed through Telegram.

Build shown by `/version`:

```text
pyro-parallel4-resume-2026-10-16-3
```

## Important: one file vs four streams

The queue is still sequential:

```text
hitek.7z.001  <- active (4 range workers inside this file)
hitek.7z.002  <- waiting
hitek.7z.003  <- waiting
...
```

`.002` does not start until `.001` is fully downloaded and assembled.

Parallel downloading can improve speed when a single Telegram connection is the bottleneck. It cannot guarantee a specific MB/s because Telegram DC throttling and the server's network route can still be the limit.

## Disk/RAM behavior

RAM stays low because each worker writes chunks directly to its own segment file.

During final assembly the segment files plus the assembled temporary file briefly coexist, so allow roughly **2x the size of the currently active part** as temporary free disk. With 2 GB archive parts, several extra GB free is enough.

## Ubuntu prerequisites

```bash
apt-get update
apt-get install -y python3 python3-venv p7zip-full screen
```

## `.env`

Keep your real `.env` out of GitHub. Example:

```env
BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_API_ID=YOUR_API_ID
TELEGRAM_API_HASH=YOUR_API_HASH
ADMIN_IDS=YOUR_TELEGRAM_USER_ID

UPLOAD_DIR=./uploads
EXTRACT_DIR=./extracted
STATE_DB=./queue.db
SESSION_DIR=./session

PARALLEL_STREAMS=4
PROGRESS_INTERVAL=3
```

`PARALLEL_STREAMS=4` is recommended first. If Telegram starts throwing network/flood errors, use `2`. Going above 4 does not automatically mean faster downloads.

## Start

Foreground:

```bash
cd /root/db2
./start.sh
```

Background with screen:

```bash
cd /root/db2
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

## After GitHub update

```bash
cd /root/db2
git pull
./stop.sh
./run_screen.sh
```

`start.sh` installs/updates the requirements automatically.

## Commands

```text
/start
/ping
/version
/whoami

/upload_on
/upload_off
/upload_status

/queue
/retryfailed
/cancelcurrent CONFIRM
/files
/parts
/disk

/test7z
/info7z
/extract7z CONFIRM
```

## Recommended workflow

```text
/upload_on
```

Forward all archive parts. Order does not matter.

Then:

```text
/upload_off
/queue
```

When queued/downloading reaches zero:

```text
/parts
/test7z
/extract7z CONFIRM
```

## Resume behavior

For a file such as `hitek.7z.001`, partial data is stored as hidden working-style files such as:

```text
hitek.7z.001.downloading.seg00
hitek.7z.001.downloading.seg01
hitek.7z.001.downloading.seg02
hitek.7z.001.downloading.seg03
```

Do **not** manually delete these while you want resume support.

If the bot is restarted, its SQLite queue changes an interrupted `downloading` job back to `queued`. When that job starts again, every segment resumes from its last complete 1 MiB chunk. At worst, less than 1 MiB per interrupted segment is downloaded again.

If a job is marked failed, use:

```text
/retryfailed
```

The partial segments are intentionally preserved.

## Dependency note

This build uses:

```text
pyrofork==2.3.69
TgCrypto-pyrofork==1.2.8
```

The PyroFork client is configured with `max_concurrent_transmissions` equal to `PARALLEL_STREAMS`, which is required for the parallel `get_file()` range workers.
