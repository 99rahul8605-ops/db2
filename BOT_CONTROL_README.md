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

## Deployment with existing screens (low risk)

1. **Back up** your `.env`, `bot.py`, current files, and tunnel config separately. Don't touch `lookup/users_lookup.db`.
2. Stop **only the bot** screen `hitekbot` during a maintenance window, leaving `lookupapi` and `db2tunnel` running. Extract/copy updated Python/scripts over existing directory, preserving `.env`, session, database, keys, logs, uploads and downloads.
3. Execute `cd /root/db2 && ./run_screen.sh` to start the updated bot. In Telegram use `/control` and `/tunnel_status`.
4. The previously started **external** API screen `lookupapi` is not managed by the bot's PID file. Don't use `/api_restart` while it is running; it could conflict with port 8080. Migrate API ownership during a maintenance window or use systemd.
5. Same for external `db2tunnel` screen: `/tunnel_status` detects it, but `/tunnel_stop` refuses to kill an unowned process. When switching ownership, manually stop old tunnel once, then `/tunnel_start`.

## Optional startup on reboot (systemd host only)

`sudo bash install_autostart.sh` installs and **enables but does not start** `db2-bot`, `db2-api`, `db2-tunnel`. Avoid starting services until conflicting screens are stopped. If systemd is unavailable (common in Docker/RDP containers), arrange startup using hosting provider's container restart/startup feature; a powered-off host cannot receive Telegram commands. If systemd is used, manage systemd API/tunnel services using `systemctl`, not bot-managed start/stop commands.

## Scope and safety

- Index recovery only runs if metadata phase is `indexing`, not `ready`, and no importer process registered. This protects completed data.
- `/cleanup_zip CONFIRM` deletes **only** `ZIP_PATH` (when DB ready and no import/indexing). Never cleans databases or sessions.
- Remote shell execution, automatic backups, cloud credentials management, raw unrestricted SQLite commands and VPS power-on are intentionally excluded.
- API exposes sensitive personal records. Apply Cloudflare Access, least privilege and HTTPS; revoke previously exposed keys. Do not expose the bot token or `.env`.
- `io` samples actual Linux `/proc/PID/io` over two seconds. `index_status` reads recovery logs.
- Existing API on RDP screen will continue regardless of new bot deployment unless manually stopped.
