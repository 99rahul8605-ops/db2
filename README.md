# DB2 Telegram Control Bot (low-memory indexing fix)

A single existing PyroFork admin bot controls uploads, archive validation/extraction, resumable ZIP-to-SQLite import, low-RAM indexing, API and Cloudflare Tunnel processes, monitoring, and API keys. **No database, uploads, passwords, keys, or credentials are included in this ZIP.**

## Installation / update (one shell file)

Do **not** update during an active import/index run. Once current `/root/index_recovery.log` shows `SUCCESS`, copy this release's files into `/root/db2`, preserving all runtime data and `.env`.

```bash
cd /root/db2
# First upload db2-bot-fixed.zip to /root/db2
mkdir -p /root/db2-backup
cp -a bot.py import_users.py recover_indexes_lowram.py ops_control.py run.sh /root/db2-backup/ 2>/dev/null || true
unzip -oq db2-bot-fixed.zip -d /tmp/db2-bot-fixed
cp /tmp/db2-bot-fixed/db2-main/*.py /root/db2/
cp /tmp/db2-bot-fixed/db2-main/requirements.txt /root/db2/
cp /tmp/db2-bot-fixed/db2-main/run.sh /root/db2/
bash run.sh restart
```

**Do not overwrite `.env`, `api_keys.db`, `queue.db`, `session/`, `uploads/`, `extracted/`, `lookup/`, tunnel credentials or running screen sessions for API/Tunnel.** One-time optional `bash run.sh tidy` moves old `.sh` files out of the project root into a backup folder. It does not delete data.

`bash run.sh` (or `bash run.sh start`) creates a missing virtual environment, installs required Python modules, installs `screen` and 7-Zip if necessary, and starts the bot in a background screen. `bash run.sh status`, `logs`, `follow`, `stop`, `restart`, `foreground` are supported. `run.sh` does **not** power on an offline VPS.

## Index fix

- `/buildall CONFIRM`: checks for ready DB or existing indexing checkpoint *before* touching archives. Imports records once; after import, the process **execs** the low-memory index recovery script instead of SQLite's high-RAM index creation.
- `/import_start`: starts/resumes import, or starts **index-only** recovery if the phase is `indexing`.
- `/index_start CONFIRM`: manually starts index-only recovery when phase is `indexing`; refuses parallel import.
- `/index_status`: shows 3 index names, completed status when readable and active **Python** PID; says when database is locked rather than guessing.
- `/index_logs`, `/import_status`, `/io`: show recovery log, importer log and live read/write MiB/s from actual indexing Python worker.
- If the importer exits during `indexing`, the bot attempts isolated low-RAM recovery **once** and reports success/failure to Telegram admins. Existing externally started recovery is detected, never duplicated.
- Each index is separately committed; interrupted jobs skip previously completed indexes. The SQL data is **not re-imported**.

## Other commands

`/control`, `/workflow`, `/public_url`, `/set_public_url https://dbapi.teamsecret.in`, `/test7z`, `/extract7z CONFIRM`, `/parts`, `/upload_on`, `/upload_off`, `/api_start`, `/api_status`, `/api_stop CONFIRM`, `/api_restart`, `/tunnel_start`, `/tunnel_status`, `/tunnel_stop CONFIRM`, `/tunnel_restart`, `/server_status`, `/disk`, `/ram`, `/cleanup` (dry-run), and existing key commands remain.

Cloudflare domain is supplied via `.env` `PUBLIC_API_URL=https://dbapi.teamsecret.in`, or set from Telegram as an admin with `/set_public_url https://dbapi.teamsecret.in` (immediately updates the displayed `/workflow` and `/public_url` values and persists to `.env`). This only affects status display, and must be configured with Cloudflare separately. Tunnel config can be selected with `TUNNEL_CONFIG` in `.env`. Access to sensitive lookup APIs should be limited via HTTPS, Cloudflare Access, admin-only bot controls and tightly scoped API keys.

## Diagnostics

```bash
bash run.sh status
bash run.sh logs
tail -f /root/index_recovery.log
pidstat -d -p "$(pgrep -f 'python3 -u recover_indexes_lowram.py' | head -1)" 2
```

Never re-run `/buildall` or delete the SQLite database during an active recovery. On hosts with less than 1 GiB RAM, index construction can take a long time, and no reliable numeric percentage exists mid-index.
