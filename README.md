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


## Public IP:port access

This build can expose the FastAPI service on the server's public IP while keeping every
lookup endpoint protected by API keys.

From the same Telegram bot:

```text
/public_on CONFIRM
/public_url
/api_status
```

`/public_url` returns an address like:

```text
http://203.0.113.10:8080
```

Remote client example:

```bash
curl -H "X-API-Key: YOUR_GENERATED_KEY" \
  "http://SERVER_PUBLIC_IP:8080/lookup?number=9999999891"
```

Turn public binding off:

```text
/public_off CONFIRM
```

Default `.env.example` settings:

```env
API_HOST=0.0.0.0
API_PORT=8080
RATE_LIMIT_PER_MIN=60
```

If public-IP auto detection is wrong, set:

```env
PUBLIC_HOST=YOUR_PUBLIC_IP
```

### Network/provider firewall

The application can bind to `0.0.0.0:8080`, but your hosting provider may still block
incoming TCP/8080. In that case, allow TCP port 8080 in the provider firewall/security
group. Do not open any database files or SQLite ports.

### Security note

API keys are mandatory and can be generated/revoked from the Telegram bot. The API has
no Swagger/Redoc pages, applies a per-key rate limit, and marks responses `no-store`.

Raw `http://IP:PORT` does **not encrypt the API key or response in transit**. It is okay
for controlled testing, but HTTPS is safer for use across untrusted networks.

## Single script
Use `bash run.sh` to prepare venv and launch the bot in a screen session. See `BOT_CONTROL_README.md` for status, logs and restart commands.
