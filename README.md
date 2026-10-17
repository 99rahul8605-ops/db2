# Hitek Private Lookup + API Key Manager

Complete private stack:

- streams `users_data.json` directly from `/root/db2/extracted/users_data.zip`
- stores all observed fields: `_id`, `mobile`, `name`, `fname`, `address`, `alt`, `circle`, `id`, `email`
- indexes `mobile`, `alt`, and `id`
- private FastAPI with generated API keys
- Telegram admin manager for key generation/revoke/expiry/stats
- direct admin `/lookup <number>`

## Important
Your upload bot already polls Telegram. Use a **separate BotFather token** for `MANAGER_BOT_TOKEN`; two polling programs cannot reliably share one bot token.

## Install

```bash
bash install.sh
nano .env
```

Required `.env` values:

```env
MANAGER_BOT_TOKEN=NEW_SEPARATE_MANAGER_BOT_TOKEN
ADMIN_IDS=YOUR_TELEGRAM_NUMERIC_ID
ZIP_PATH=/root/db2/extracted/users_data.zip
DB_PATH=/root/db2/lookup/users_lookup.db
KEYS_DB_PATH=/root/db2/lookup/api_keys.db
BIND_HOST=127.0.0.1
BIND_PORT=8080
SHOW_FULL_FIELDS=1
```

## Optional quick test: first 100k records

```bash
./venv/bin/python3 import_users.py --fresh --limit 100000
./start_all.sh
```

Later rebuild full DB:

```bash
./venv/bin/python3 import_users.py --fresh
```

## Full import in background

```bash
./run_import_screen.sh
```

Watch:

```bash
tail -f logs/import.log
```

The 446 GiB JSON is not extracted to disk first. If import stops, run the importer again; it uses the last committed record checkpoint (the Deflate stream still has to be re-read to reach that point).

## Start API + manager bot

```bash
./start_all.sh
./status.sh
```

Manager commands:

```text
/genkey <name> [days|never]
/keys [limit]
/key <id>
/disable <id>    (or /revoke <id>)
/enable <id>
/rename <id> <new name>
/expire <id> <days|never>
/delete <id> CONFIRM
/lookup <number>
/id <record_id>
/stats
/health
/whoami
```

Raw generated keys are shown only once; only SHA-256 hashes are stored.

## API example
Generate a key with `/genkey main_bot 30`, then:

```bash
curl -s -H 'X-API-Key: hk_...' 'http://127.0.0.1:8080/lookup?number=9999999891'
```

Endpoints:

```text
GET /lookup?number=...
GET /user/{mobile}
GET /id/{record_id}
GET /stats
GET /health     (no key; status only)
```

## Existing upload bot
After `/genkey`, put the generated key in your upload bot `.env`:

```env
LOOKUP_API_URL=http://127.0.0.1:8080
LOOKUP_API_KEY=hk_...
```

Then adapt `pyrogram_lookup_snippet.py` into the existing bot.

## CLI fallback

```bash
./venv/bin/python3 key_cli.py generate main_bot --days 30
./venv/bin/python3 key_cli.py list
./venv/bin/python3 key_cli.py disable 1
./venv/bin/python3 key_cli.py enable 1
```

Keep the API on `127.0.0.1` when your bot is on the same server. Do not commit `.env`, raw API keys, or generated databases to Git.
