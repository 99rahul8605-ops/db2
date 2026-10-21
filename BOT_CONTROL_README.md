# DB2 Telegram control extension

Maintains the existing PyroFork Telegram bot. All commands require `ADMIN_IDS` membership. No existing ZIPs, database or secrets are bundled. Import/data files are NOT copied into the release.

## Commands

| Area | Commands |
| --- | --- |
| Overview | `/control`, `/workflow`, `/server_status`, `/disk`, `/ram` |
| Upload / source | `/upload_on`, `/upload_off`, `/parts`, `/test7z`, `/extract7z CONFIRM` |
| Import | `/buildall CONFIRM`, `/import_start`, `/import_status`, `/import_stop CONFIRM` |
| Index recovery | `/index_status`, `/index_logs`, `/index_start CONFIRM`, `/io` |
| API | `/api_start`, `/api_status`, `/api_restart`, `/api_stop CONFIRM`, `/api_logs` |
| Cloudflare | `/tunnel_status`, `/tunnel_start`, `/tunnel_restart`, `/tunnel_stop CONFIRM`, `/tunnel_logs` |
| Keys/search | Existing `/genkey`, `/keys`, `/lookup`, `/id`, etc. |
| Cleanup | `/cleanup` (dry run), `/cleanup_zip CONFIRM` (deletes only source ZIP) |

## One-command setup and startup

`bash run.sh` automatically creates a missing `venv`, installs dependencies when needed, checks `.env` and starts `hitekbot` in screen. It does not delete or modify database, API keys, tunnel or old screen sessions.

```bash
cd /root/db2
bash run.sh                 # setup if needed + start bot
bash run.sh status          # screen status
bash run.sh logs            # last 70 log lines
bash run.sh follow          # live log; Ctrl+C only exits log view
bash run.sh restart         # bot only
bash run.sh stop            # bot only
bash run.sh foreground      # debug errors
```

Keep existing `lookupapi` and `db2tunnel` sessions running. Bot operations can control separately managed processes only according to their ownership checks. If this is a new container, the `.env`, sessions, database and tunnel credentials must be restored separately. If host/container is powered off, the bot cannot receive commands; container-level autostart is a separate hosting setting.

**Sensitive API:** require Cloudflare Access/restricted access; never publish API keys, bot token or `.env`.
