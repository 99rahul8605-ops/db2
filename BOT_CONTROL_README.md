# Bot commands and service startup

- `/control`: list Telegram commands (admin-only).
- `/buildall CONFIRM`, `/import_start`, `/import_status`, `/import_stop CONFIRM`: import pipeline.
- `/index_status`, `/index_logs`, `/index_start CONFIRM`, `/io`: three-index recovery and read/write monitoring.
- `/api_start`, `/api_status`, `/api_restart`, `/api_stop CONFIRM`, `/api_logs`: FastAPI management.
- `/tunnel_start`, `/tunnel_status`, `/tunnel_restart`, `/tunnel_stop CONFIRM`, `/tunnel_logs`: Cloudflare tunnel management.
- `/server_status`, `/disk`, `/ram`, `/bot_logs`, `/cleanup`: monitoring and safe cleanup preview.

Only **one** shell script is shipped: `bash run.sh [start|setup|status|logs|follow|stop|restart|foreground|tidy]`.

The bot requires the container to be powered on. Starting a completely powered-off VPS requires the hosting provider's infrastructure; a Telegram bot inside the container cannot power it on. Existing unmanaged screen API/tunnel instances are left untouched to avoid accidental downtime.
