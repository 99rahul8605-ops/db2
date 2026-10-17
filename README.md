# Hitek One-Bot Control Panel

One Telegram bot controls the whole workflow: large parallel uploads, 7z validation/extraction, ZIP→SQLite import/indexing, local API process, API keys, and direct lookups.

## First install only

```bash
bash install.sh
nano .env
./run_screen.sh
```

## Normal workflow from Telegram

1. `/upload_on` then forward all archive parts.
2. `/parts` to verify all volumes.
3. Send:

```text
/buildall CONFIRM
```

That single command will:

- turn uploads OFF
- test/extract the split 7z if `users_data.zip` is not already present
- start/resume streaming import from `users_data.zip` (the 446 GiB JSON is not separately extracted)
- build indexes on `mobile`, `alt`, and `id`
- automatically start the private FastAPI service when indexing finishes
- notify admins when ready

Import runs as a detached server process, so closing RDP/laptop does not stop it. Its checkpoint is stored in SQLite.

## Bot control commands

```text
/buildall CONFIRM
/workflow
/import_start
/import_status
/import_stop CONFIRM
/api_start
/api_status
/api_stop CONFIRM
/logs import 30
/logs api 30
```

Existing upload/key/lookup commands remain available:

```text
/upload_on /upload_off /queue /retryfailed
/parts /test7z /extract7z CONFIRM
/genkey <name> [days|never] /keys /disable /enable
/lookup <number> /id <record_id> /stats
```

API binds to `127.0.0.1` by default. API keys are managed by the same Telegram bot.
