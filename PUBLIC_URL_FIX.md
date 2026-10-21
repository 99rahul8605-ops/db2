# Public URL fix (2026-10-22)

If `/workflow` says `Public URL: http://152.55.180.95:8080`, that is the NAT/public-IP fallback and not your working Cloudflare hostname.

From Telegram (authorized admin):

```
/set_public_url https://dbapi.teamsecret.in
/public_url
/workflow
```

The bot saves `PUBLIC_API_URL=https://dbapi.teamsecret.in` to `.env`. No restart is needed for this command after installing the updated bot. It does not start Cloudflare Tunnel or change DNS; check `/tunnel_status` separately.

To install, wait for active indexing to finish. Back up `bot.py`, copy updated `.py` files from the ZIP and restart only the bot with `bash run.sh restart`. Do not overwrite `.env`, database, credentials, or active tunnel/API screens.

For an earlier bot without `/set_public_url`, set `PUBLIC_API_URL=https://dbapi.teamsecret.in` manually in `.env`, then restart the bot after indexing completes.
