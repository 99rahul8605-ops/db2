# Hitek Pyrogram Parallel Downloader — Retry/Resume build

Build: `pyro-parallel4-retry-2026-10-16-4`

- Queue still runs **one Telegram file at a time**.
- The active file uses 4 parallel ranges by default.
- Each range automatically retries if Telegram media DC times out or the ranged generator ends early.
- Retry resumes from the last completed 1 MiB chunk; already completed ranges/chunks are not downloaded again.
- Partial segment files survive bot restarts and `/retryfailed` resumes them.
- Upload mode starts OFF. Use `/upload_on` before forwarding files.

Optional `.env` tuning:

```env
PARALLEL_STREAMS=4
STREAM_RETRIES=12
STREAM_RETRY_DELAY=2
PROGRESS_INTERVAL=3
```

For a flaky Telegram route, keep `PARALLEL_STREAMS=4` and raise `STREAM_RETRIES` first. If repeated timeouts continue, try `PARALLEL_STREAMS=3` rather than increasing parallelism.

After updating:

```bash
cd /root/db2
./stop.sh
./run_screen.sh
```

Then use `/version`. Existing partial `.segXX` files are intentionally kept and will be reused.
