import asyncio
import logging
import os
import fcntl
import re
import shutil
import sqlite3
import time
import subprocess
import signal
import urllib.request
import json
import socket
from contextlib import suppress
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pyrogram import Client, filters, idle, enums
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import Message
from pyrogram.file_id import FileId

import key_store
import ops_control as ops

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("hitek-unified-manager")
BUILD = "db2-onebot-lowram-autorecovery-public-url-2026-10-22"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ID = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
}
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(ROOT / "uploads"))).resolve()
EXTRACT_DIR = Path(os.getenv("EXTRACT_DIR", str(ROOT / "extracted"))).resolve()
STATE_DB = Path(os.getenv("STATE_DB", str(ROOT / "queue.db"))).resolve()
SESSION_DIR = Path(os.getenv("SESSION_DIR", str(ROOT / "session"))).resolve()
KEYS_DB = Path(os.getenv("KEYS_DB", str(ROOT / "api_keys.db"))).resolve()
LOOKUP_DB = Path(os.getenv("LOOKUP_DB", str(ROOT / "lookup" / "users_lookup.db"))).resolve()
MAX_LOOKUP_RESULTS = max(1, min(50, int(os.getenv("MAX_LOOKUP_RESULTS", "10") or 10)))
PARALLEL_STREAMS = max(1, min(8, int(os.getenv("PARALLEL_STREAMS", "4") or 4)))
PROGRESS_INTERVAL = max(2.0, float(os.getenv("PROGRESS_INTERVAL", "3") or 3))
STREAM_RETRIES = max(1, min(50, int(os.getenv("STREAM_RETRIES", "12") or 12)))
STREAM_RETRY_DELAY = max(0.5, float(os.getenv("STREAM_RETRY_DELAY", "2") or 2))
TG_CHUNK_SIZE = 1024 * 1024  # Pyrogram get_file() range unit is 1 MiB
PID_FILE = ROOT / ".bot.pid"
LOCK_FILE = ROOT / ".bot.lock"
IMPORT_PID_FILE = ROOT / ".import.pid"
API_PID_FILE = ROOT / ".api.pid"
IMPORT_LOG = ROOT / "import.log"
API_LOG = ROOT / "api.log"
ZIP_PATH = Path(os.getenv("ZIP_PATH", str(EXTRACT_DIR / "users_data.zip"))).resolve()
API_HOST = os.getenv("API_HOST", "127.0.0.1").strip() or "127.0.0.1"
API_PORT = int(os.getenv("API_PORT", "8080") or 8080)
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "").strip()
PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", "").strip().rstrip("/")
RATE_LIMIT_PER_MIN = max(1, min(10000, int(os.getenv("RATE_LIMIT_PER_MIN", "60") or 60)))
SEVEN_ZIP = shutil.which("7z") or shutil.which("7zz") or "7z"
_INSTANCE_LOCK_FH = None

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN missing in .env")
if not API_ID:
    raise SystemExit("TELEGRAM_API_ID missing/invalid in .env")
if not API_HASH:
    raise SystemExit("TELEGRAM_API_HASH missing in .env")
if not ADMIN_IDS:
    raise SystemExit("ADMIN_IDS missing in .env")

for p in (UPLOAD_DIR, EXTRACT_DIR, SESSION_DIR, LOOKUP_DB.parent):
    p.mkdir(parents=True, exist_ok=True)
STATE_DB.parent.mkdir(parents=True, exist_ok=True)
KEYS_DB.parent.mkdir(parents=True, exist_ok=True)


def acquire_instance_lock():
    global _INSTANCE_LOCK_FH
    fh = open(LOCK_FILE, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise SystemExit(
            "Another hitek bot instance is already running. Stop it first with bash run.sh stop"
        )
    _INSTANCE_LOCK_FH = fh
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def release_instance_lock():
    global _INSTANCE_LOCK_FH
    with suppress(Exception):
        if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink()
    if _INSTANCE_LOCK_FH is not None:
        with suppress(Exception):
            fcntl.flock(_INSTANCE_LOCK_FH.fileno(), fcntl.LOCK_UN)
        with suppress(Exception):
            _INSTANCE_LOCK_FH.close()
        _INSTANCE_LOCK_FH = None


app = Client(
    "hitek_upload_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir=str(SESSION_DIR),
    workers=8,
    sleep_threshold=20,
    max_concurrent_transmissions=PARALLEL_STREAMS,
)

# File names and command output can contain Markdown/HTML-like characters.
# Disable parsing globally so progress edits never trigger parser warnings.
app.set_parse_mode(enums.ParseMode.DISABLED)

# Safety: uploads are OFF after every process restart.
upload_mode = False
operation_lock = asyncio.Lock()
worker_task = None
current_job_id = None
current_name = None
current_started = None
cancel_current = False
pipeline_task = None
index_watch_task = None


def _read_pid(path: Path):
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _pid_alive(pid):
    """A zombie PID is dead; os.kill(pid, 0) alone cannot detect that."""
    if not pid:
        return False
    try:
        status = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return status.rsplit(") ", 1)[1].split()[0] not in {"Z", "X"}
    except (FileNotFoundError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    except (OSError, IndexError):
        return False


def _proc_status(pid_file: Path):
    pid = _read_pid(pid_file)
    if pid and _pid_alive(pid):
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            expected = ("import_users.py", "recover_indexes_lowram.py") if pid_file == IMPORT_PID_FILE else ("uvicorn", "api:app")
            if any(part in cmd for part in expected):
                return True, pid
        except OSError:
            pass
    if pid_file.exists():
        with suppress(Exception):
            pid_file.unlink()
    return False, None


def _start_detached(cmd, pid_file: Path, log_file: Path):
    alive, pid = _proc_status(pid_file)
    if alive:
        return False, pid
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_file, "ab", buffering=0)
    proc = subprocess.Popen(
        [str(x) for x in cmd],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    log_fh.close()
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    # Surface broken commands early rather than reporting a successful start.
    time.sleep(0.25)
    if proc.poll() is not None:
        pid_file.unlink(missing_ok=True)
        raise RuntimeError(f"Process exited with code {proc.returncode}. Check {log_file}.")
    return True, proc.pid


def _stop_detached(pid_file: Path):
    alive, pid = _proc_status(pid_file)
    if not alive:
        return False, None
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception:
        with suppress(Exception):
            os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    if _pid_alive(pid):
        with suppress(Exception):
            os.killpg(pid, signal.SIGKILL)
    with suppress(Exception):
        pid_file.unlink()
    return True, pid


def _tail(path: Path, lines=25, max_chars=3500):
    if not path.exists():
        return "(no log yet)"
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            text = f.read().decode(errors="replace")
        out = "\n".join(text.splitlines()[-max(1, lines):])
        return out[-max_chars:]
    except Exception as e:
        return f"log read error: {e}"


def _lookup_meta():
    meta = {}
    if not LOOKUP_DB.exists():
        return meta
    try:
        con = sqlite3.connect(f"file:{LOOKUP_DB}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute("SELECT key,value FROM import_meta").fetchall()
            meta = {str(k): str(v) for k, v in rows}
        finally:
            con.close()
    except Exception:
        pass
    return meta


def _lookup_ready():
    meta = _lookup_meta()
    return LOOKUP_DB.exists() and meta.get("completed") == "1" and meta.get("phase") in {None, "ready"}


def _start_index_recovery():
    """Use only the committed SQLite checkpoint; never re-import the JSON."""
    py = ROOT / "venv" / "bin" / "python3"
    if not py.is_file():
        raise RuntimeError("venv Python missing; run bash run.sh setup")
    if not (ROOT / "recover_indexes_lowram.py").is_file():
        raise RuntimeError("recover_indexes_lowram.py is missing")
    meta = _lookup_meta()
    if meta.get("phase") != "indexing":
        raise RuntimeError(f"Refusing index-only recovery: phase={meta.get('phase')!r}")
    if not LOOKUP_DB.is_file():
        raise RuntimeError("Database file missing")
    import_alive, import_pid = _proc_status(IMPORT_PID_FILE)
    if import_alive:
        return "import/index already running", import_pid
    existing = ops.matches("recover_indexes_lowram.py")
    if existing:
        return "index recovery already running", existing[0]
    # This log is separate from import.log so /index_status always finds it.
    return ops.start("index", [str(py), "-u", str(ROOT / "recover_indexes_lowram.py"),
                               "--db", str(LOOKUP_DB), "--run"],
                     ops.INDEX_LOG, "recover_indexes_lowram.py")


def _start_import():
    meta = _lookup_meta()
    if meta.get("completed") == "1" and meta.get("phase") == "ready":
        return "complete", None
    # Critical: a finished import with missing indexes must never rescan 446 GiB JSON.
    if meta.get("phase") == "indexing":
        return _start_index_recovery()
    python = ROOT / "venv" / "bin" / "python3"
    if not python.is_file():
        raise RuntimeError("venv missing; run bash run.sh setup once")
    other = ops.matches("recover_indexes_lowram.py")
    if other:
        return "index recovery already running", other[0]
    if not ZIP_PATH.is_file():
        raise RuntimeError(f"Source ZIP not found: {ZIP_PATH}")
    started, pid = _start_detached([python, ROOT / "import_users.py"], IMPORT_PID_FILE, IMPORT_LOG)
    return ("started" if started else "running"), pid


def _api_health_ok():
    """Check a pre-existing manually started local API without trusting a stale PID."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/health", timeout=1.5) as response:
            data = json.loads(response.read(512))
        return response.status == 200 and data.get("ok") is True
    except Exception:
        return False


def _start_api():
    if not _lookup_ready():
        raise RuntimeError("Lookup DB is not fully imported/indexed yet")
    alive, pid = _proc_status(API_PID_FILE)
    if alive:
        return "already running (managed)", pid
    if _api_health_ok():
        # Do not start a duplicate process against an external screen-managed API.
        return "already running (external; not managed by bot)", None
    try:
        with socket.create_connection(("127.0.0.1", API_PORT), timeout=0.5):
            raise RuntimeError(f"TCP port {API_PORT} already in use by another service")
    except (ConnectionRefusedError, TimeoutError, OSError):
        pass
    uvicorn = ROOT / "venv" / "bin" / "uvicorn"
    if not uvicorn.is_file():
        raise RuntimeError("uvicorn missing; run bash run.sh setup once")
    started, pid = _start_detached(
        [uvicorn, "api:app", "--host", API_HOST, "--port", str(API_PORT), "--workers", "1"],
        API_PID_FILE, API_LOG,
    )
    return ("started" if started else "running"), pid



def _set_env_value(key: str, value: str):
    """Persist a simple KEY=value setting without touching secrets."""
    path = ROOT / ".env"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    out = []
    found = False
    for line in lines:
        if line.startswith(key + "="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        if out and out[-1] != "":
            out.append("")
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _public_ip():
    if PUBLIC_HOST:
        return PUBLIC_HOST
    try:
        req = urllib.request.Request(
            "https://api.ipify.org",
            headers={"User-Agent": "hitek-bot/1.0"},
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            ip = r.read(128).decode("ascii", errors="ignore").strip()
        if re.fullmatch(r"[0-9a-fA-F:.]+", ip):
            return ip
    except Exception:
        pass
    return None


def _public_url():
    # A Cloudflare hostname is preferred: host/container public IP often uses NAT.
    if PUBLIC_API_URL.startswith("https://"):
        return PUBLIC_API_URL
    if API_HOST == "0.0.0.0":
        ip = _public_ip()
        return f"http://{ip}:{API_PORT}" if ip else None
    return None


def _api_urls_text():
    public = _public_url()
    bind = f"http://{API_HOST}:{API_PORT}"
    lines = [f"Bind: {bind}", f"Local: http://127.0.0.1:{API_PORT}"]
    if PUBLIC_API_URL.startswith("https://"):
        lines.append(f"Cloudflare URL: {PUBLIC_API_URL} (requires active tunnel)")
    elif API_HOST == "0.0.0.0":
        lines.append(f"Direct IP: {public or 'unknown'} (requires provider port forwarding)")
    else:
        lines.append("Direct public IP: OFF (localhost-only)")
    lines.append(f"Per-key rate limit: {RATE_LIMIT_PER_MIN}/minute")
    return "\n".join(lines)


def _restart_api_if_running():
    alive, _ = _proc_status(API_PID_FILE)
    if alive:
        _stop_detached(API_PID_FILE)
        if _lookup_ready():
            return _start_api()
    return ("configured", None)


def _workflow_text():
    import_alive, import_pid = _proc_status(IMPORT_PID_FILE)
    api_alive, api_pid = _proc_status(API_PID_FILE)
    meta = _lookup_meta()
    zip_state = f"✅ {human_size(ZIP_PATH.stat().st_size)}" if ZIP_PATH.exists() else "❌ missing"
    db_state = f"✅ {human_size(LOOKUP_DB.stat().st_size)}" if LOOKUP_DB.exists() else "❌ missing"
    phase = meta.get("phase", "-")
    processed = meta.get("processed_records", "0")
    inserted = meta.get("inserted_records", "0")
    rate = meta.get("last_rate", "-")
    return (
        "🧭 Pipeline status\n"
        f"Source ZIP: {zip_state}\n"
        f"Lookup DB: {db_state}\n"
        f"Phase: {phase}\n"
        f"Processed: {processed}\n"
        f"Inserted: {inserted}\n"
        f"Rate: {rate} rec/s\n"
        f"Import/index worker: {'🟢 PID '+str(import_pid) if import_alive else '⚪ stopped'}\n"
        f"Index recovery PIDs: {ops.matches('recover_indexes_lowram.py') or '-'}\n"
        f"API: {'🟢 PID '+str(api_pid) if api_alive else '⚪ stopped'}\n"
        f"API bind: http://{API_HOST}:{API_PORT}\n"
        f"Public URL: {_public_url() or '-'}"
    )


async def _notify_admins(text: str):
    for uid in ADMIN_IDS:
        with suppress(Exception):
            await app.send_message(uid, text)


async def _pipeline_monitor():
    """Watch detached import/index; resume interrupted index stage at most once."""
    last_notice = time.monotonic()
    recovery_attempts = 0
    while True:
        meta = await asyncio.to_thread(_lookup_meta)
        if meta.get("completed") == "1" and meta.get("phase") == "ready":
            try:
                state, pid = await asyncio.to_thread(_start_api)
                await _notify_admins(
                    f"✅ Lookup DB import + all indexes complete.\nAPI {state} (PID {pid}).\n"
                    "Use /index_status or /workflow"
                )
            except Exception as e:
                await _notify_admins(f"⚠️ DB ready but API start failed: {type(e).__name__}: {e}")
            return

        import_alive, _ = _proc_status(IMPORT_PID_FILE)
        other_recovery = ops.matches("recover_indexes_lowram.py")
        if not import_alive and not other_recovery:
            # SQLite may still be releasing locks on exit.
            await asyncio.sleep(2)
            meta = await asyncio.to_thread(_lookup_meta)
            if meta.get("completed") == "1" and meta.get("phase") == "ready":
                continue
            if meta.get("phase") == "indexing" and recovery_attempts < 1:
                recovery_attempts += 1
                try:
                    state, pid = await asyncio.to_thread(_start_index_recovery)
                    await _notify_admins(
                        f"⚠️ Import/index worker exited in indexing phase.\n"
                        f"✅ Low-RAM index-only recovery {state} (PID {pid}).\n"
                        "No JSON re-import. Use /index_status and /index_logs."
                    )
                    continue
                except Exception as e:
                    await _notify_admins(f"❌ Automatic index recovery failed: {type(e).__name__}: {e}")
            await _notify_admins(
                "❌ Import/index worker stopped before DB was ready.\n"
                f"{_workflow_text()}\n\nImport log:\n{_tail(IMPORT_LOG, 12, 1800)}\n"
                f"Index log:\n{_tail(ops.INDEX_LOG, 8, 1100)}\n"
                "Use /index_status; recovery can be restarted with /index_start CONFIRM."
            )
            return

        now = time.monotonic()
        if now - last_notice >= 1800:
            await _notify_admins(_workflow_text())
            last_notice = now
        await asyncio.sleep(20)


def _ensure_pipeline_monitor():
    global pipeline_task
    if pipeline_task is None or pipeline_task.done():
        pipeline_task = asyncio.create_task(_pipeline_monitor(), name="pipeline-monitor")


def db_conn():
    con = sqlite3.connect(STATE_DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db():
    with db_conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                file_id TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_queue_status_id ON queue(status, id);
            """
        )
        # Schema migration for queues created by older versions.
        cols = {r[1] for r in con.execute("PRAGMA table_info(queue)").fetchall()}
        if "file_id" not in cols:
            con.execute("ALTER TABLE queue ADD COLUMN file_id TEXT")

        # Recover interrupted jobs after restart.
        con.execute(
            "UPDATE queue SET status='queued', error=NULL, updated_at=? WHERE status='downloading'",
            (time.time(),),
        )


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.2f} {unit}"
        n /= 1024


def fmt_eta(seconds):
    if seconds is None or seconds <= 0 or seconds > 365 * 86400:
        return "--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def progress_bar(done, total, width=14):
    if not total:
        return "░" * width
    ratio = min(1.0, max(0.0, done / total))
    full = int(ratio * width)
    return "█" * full + "░" * (width - full)


def safe_filename(name):
    name = Path(name or "file.bin").name
    name = re.sub(r"[^A-Za-z0-9._()\- ]", "_", name)
    return name[:240] or "file.bin"


def is_admin(message: Message):
    return bool(message.from_user and message.from_user.id in ADMIN_IDS)


def admin_only(func):
    @wraps(func)
    async def wrapper(client: Client, message: Message):
        if not is_admin(message):
            with suppress(Exception):
                await message.reply_text("⛔ Admin only.")
            return
        try:
            return await func(client, message)
        except Exception as e:
            log.exception("Handler failed: %s", func.__name__)
            with suppress(Exception):
                await message.reply_text(f"❌ {type(e).__name__}: {e}")
    return wrapper


async def edit_safe(message: Message, text: str):
    try:
        await message.edit_text(text)
    except MessageNotModified:
        pass
    except FloodWait as e:
        await asyncio.sleep(int(e.value) + 1)
        with suppress(Exception):
            await message.edit_text(text)
    except Exception as e:
        log.debug("Progress edit skipped: %s: %s", type(e).__name__, e)


def queue_counts():
    with db_conn() as con:
        rows = con.execute(
            "SELECT status, COUNT(*) c FROM queue GROUP BY status"
        ).fetchall()
    counts = {r["status"]: r["c"] for r in rows}
    return counts


def add_job(chat_id, message_id, file_name, file_size, file_id=None):
    now = time.time()
    with db_conn() as con:
        try:
            cur = con.execute(
                """
                INSERT INTO queue(chat_id,message_id,file_name,file_size,file_id,status,created_at,updated_at)
                VALUES(?,?,?,?,?, 'queued', ?, ?)
                """,
                (chat_id, message_id, file_name, file_size, file_id, now, now),
            )
            return cur.lastrowid, True
        except sqlite3.IntegrityError:
            row = con.execute(
                "SELECT id,status,file_id FROM queue WHERE chat_id=? AND message_id=?",
                (chat_id, message_id),
            ).fetchone()
            if file_id and not row["file_id"]:
                con.execute(
                    "UPDATE queue SET file_id=?, updated_at=? WHERE id=?",
                    (file_id, now, row["id"]),
                )
            return row["id"], False


def next_job():
    with db_conn() as con:
        row = con.execute(
            "SELECT * FROM queue WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        now = time.time()
        con.execute(
            "UPDATE queue SET status='downloading', error=NULL, updated_at=? WHERE id=?",
            (now, row["id"]),
        )
        return dict(row)


def set_job_status(job_id, status, error=None):
    with db_conn() as con:
        con.execute(
            "UPDATE queue SET status=?, error=?, updated_at=? WHERE id=?",
            (status, error, time.time(), job_id),
        )


def waiting_ahead(job_id):
    with db_conn() as con:
        return con.execute(
            "SELECT COUNT(*) FROM queue WHERE status='queued' AND id < ?", (job_id,)
        ).fetchone()[0]


def files_list():
    return sorted(
        (p for p in UPLOAD_DIR.iterdir() if p.is_file() and ".downloading" not in p.name),
        key=lambda p: p.name.lower(),
    )


def detect_parts():
    pat = re.compile(r"^(?P<base>.+\.7z)\.(?P<num>\d{3,})$", re.I)
    groups = {}
    for p in files_list():
        m = pat.match(p.name)
        if m:
            groups.setdefault(m.group("base"), []).append((int(m.group("num")), p))
    return groups


def validate_parts(items):
    if not items:
        return False, "No parts found"
    nums = sorted({n for n, _ in items})
    missing = sorted(set(range(1, nums[-1] + 1)) - set(nums))
    if missing:
        preview = ", ".join(f".{n:03d}" for n in missing[:30])
        suffix = " ..." if len(missing) > 30 else ""
        return False, f"Missing {len(missing)} part(s): {preview}{suffix}"
    return True, f"{len(nums)} contiguous parts (.001-.{nums[-1]:03d})"


def choose_group(require_complete=True):
    groups = detect_parts()
    if not groups:
        raise RuntimeError("No *.7z.001 style archive found in uploads.")
    complete = []
    for base, items in groups.items():
        ok, _ = validate_parts(items)
        if ok:
            complete.append((base, sorted(items)))
    if complete:
        return sorted(complete)[0]
    base, items = sorted(groups.items())[0]
    if require_complete:
        raise RuntimeError(validate_parts(items)[1])
    return base, sorted(items)


async def run_cmd(args, cwd=None):
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace")


@app.on_message(filters.all, group=-100)
async def incoming_logger(client: Client, message: Message):
    log.info(
        "INCOMING chat=%s from=%s msg=%s text=%r document=%s",
        message.chat.id if message.chat else None,
        message.from_user.id if message.from_user else None,
        message.id,
        (message.text or message.caption or "")[:120],
        bool(message.document),
    )


@app.on_message(filters.command(["start", "help"]))
@admin_only
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        "🗄 Hitek One-Bot Control Panel\n\n"
        "ONE COMMAND PIPELINE\n"
        "/buildall CONFIRM — test/extract if needed → import/index → auto-start API\n"
        "/workflow — current end-to-end status\n\n"
        "UPLOAD\n"
        "/upload_on • /upload_off • /upload_status\n"
        "/queue • /retryfailed • /cancelcurrent CONFIRM\n"
        "/files • /parts • /test7z • /info7z • /extract7z CONFIRM\n\n"
        "IMPORT / API\n"
        "/import_start • /import_status • /import_stop CONFIRM\n"
        "/api_start • /api_status • /api_stop CONFIRM\n"
        "/public_on CONFIRM • /public_off CONFIRM • /public_url\n"
        "/set_public_url https://your.domain\n"
        "/logs import [lines] • /logs api [lines]\n\n"
        "API KEYS\n"
        "/genkey <name> [days|never] • /keys • /key <id>\n"
        "/disable <id> • /enable <id> • /rename <id> <name>\n"
        "/expire <id> <days|never> • /deletekey <id> CONFIRM\n\n"
        "LOOKUP\n"
        "/lookup <number> • /id <record_id> • /stats\n\n"
        "SYSTEM\n"
        "/disk • /ping • /whoami • /version"
    )


@app.on_message(filters.command("ping"))
@admin_only
async def ping_handler(client: Client, message: Message):
    await message.reply_text(f"✅ Pong — Pyrogram receiver is working.\nBuild: {BUILD}\nParallel streams: {PARALLEL_STREAMS}")


@app.on_message(filters.command("version"))
async def version_handler(client: Client, message: Message):
    await message.reply_text(f"Build: {BUILD}\nPID: {os.getpid()}\nParallel streams: {PARALLEL_STREAMS}")


@app.on_message(filters.command("whoami"))
async def whoami_handler(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    await message.reply_text(
        f"User ID: {uid}\nChat ID: {message.chat.id}\nAdmin: {uid in ADMIN_IDS}"
    )


@app.on_message(filters.command("upload_on"))
@admin_only
async def upload_on_handler(client: Client, message: Message):
    global upload_mode
    upload_mode = True
    await message.reply_text(
        "🟢 Upload mode ON. New documents will be queued.\n"
        "Send/forward all parts; order does not matter."
    )


@app.on_message(filters.command("upload_off"))
@admin_only
async def upload_off_handler(client: Client, message: Message):
    global upload_mode
    upload_mode = False
    c = queue_counts()
    await message.reply_text(
        "🔴 Upload mode OFF. New files will be ignored.\n"
        f"Current: {current_name or '-'}\n"
        f"Queued: {c.get('queued', 0)}"
    )


@app.on_message(filters.command("upload_status"))
@admin_only
async def upload_status_handler(client: Client, message: Message):
    c = queue_counts()
    await message.reply_text(
        f"Upload mode: {'🟢 ON' if upload_mode else '🔴 OFF'}\n"
        f"Current: {current_name or '-'}\n"
        f"Queued: {c.get('queued', 0)}\n"
        f"Done: {c.get('done', 0)} • Failed: {c.get('failed', 0)}"
    )


@app.on_message(filters.command("queue"))
@admin_only
async def queue_handler(client: Client, message: Message):
    c = queue_counts()
    text = (
        f"Current: {current_name or '-'}\n"
        f"Queued: {c.get('queued', 0)}\n"
        f"Downloading: {c.get('downloading', 0)}\n"
        f"Done: {c.get('done', 0)}\n"
        f"Failed: {c.get('failed', 0)}\nParallel streams/file: {PARALLEL_STREAMS}"
    )
    await message.reply_text(text)


@app.on_message(filters.command("retryfailed"))
@admin_only
async def retry_failed_handler(client: Client, message: Message):
    with db_conn() as con:
        cur = con.execute(
            "UPDATE queue SET status='queued',error=NULL,updated_at=? WHERE status='failed'",
            (time.time(),),
        )
    await message.reply_text(f"🔁 Requeued {cur.rowcount} failed job(s).")


@app.on_message(filters.command("cancelcurrent"))
@admin_only
async def cancel_current_handler(client: Client, message: Message):
    global cancel_current
    args = message.command or []
    if len(args) < 2 or args[1] != "CONFIRM":
        return await message.reply_text("Use: `/cancelcurrent CONFIRM`")
    if not current_job_id:
        return await message.reply_text("No active download.")
    cancel_current = True
    await message.reply_text("⚠️ Cancellation requested. Current file will be requeued.")


@app.on_message(filters.command("files"))
@admin_only
async def files_handler(client: Client, message: Message):
    fs = files_list()
    if not fs:
        return await message.reply_text("No downloaded files yet.")
    total = sum(p.stat().st_size for p in fs)
    lines = [f"Files: {len(fs)} • Total: {human_size(total)}"]
    for p in fs[:70]:
        lines.append(f"{p.name} — {human_size(p.stat().st_size)}")
    if len(fs) > 70:
        lines.append(f"... +{len(fs)-70} more")
    await message.reply_text("\n".join(lines))


@app.on_message(filters.command("parts"))
@admin_only
async def parts_handler(client: Client, message: Message):
    groups = detect_parts()
    if not groups:
        return await message.reply_text("No *.7z.001 style parts found.")
    lines = []
    for base, items in sorted(groups.items()):
        ok, msg = validate_parts(items)
        total = sum(p.stat().st_size for _, p in items)
        lines.append(f"{'✅' if ok else '⚠️'} {base}: {msg}\nTotal: {human_size(total)}")
    await message.reply_text("\n\n".join(lines))


@app.on_message(filters.command("disk"))
@admin_only
async def disk_handler(client: Client, message: Message):
    d = shutil.disk_usage(UPLOAD_DIR)
    mem_text = ""
    try:
        vals = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                k, v = line.split(":", 1)
                vals[k] = int(v.strip().split()[0]) * 1024
        total = vals.get("MemTotal", 0)
        avail = vals.get("MemAvailable", 0)
        mem_text = f"\nRAM: {human_size(total-avail)} used / {human_size(total)}"
    except Exception:
        pass
    await message.reply_text(
        f"Disk total: {human_size(d.total)}\n"
        f"Disk used: {human_size(d.used)}\n"
        f"Disk free: {human_size(d.free)}{mem_text}"
    )



async def _test_archive():
    _, items = choose_group(True)
    first = items[0][1]
    code, out = await run_cmd([SEVEN_ZIP, "t", str(first)])
    return code, out, first


async def _extract_archive(status: Message | None = None):
    _, items = choose_group(True)
    first = items[0][1]
    proc = await asyncio.create_subprocess_exec(
        SEVEN_ZIP, "x", str(first), f"-o{EXTRACT_DIR}", "-y", "-bb1", "-bsp1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    tail = ""
    last_edit = 0.0
    pct_re = re.compile(r"(\d{1,3})%")
    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            break
        txt = chunk.decode(errors="replace")
        tail = (tail + txt)[-12000:]
        now = time.monotonic()
        if status and now - last_edit >= 4:
            matches = pct_re.findall(tail.replace("\r", "\n"))
            pct = matches[-1] if matches else "working"
            free = shutil.disk_usage(EXTRACT_DIR).free
            await edit_safe(
                status,
                f"📦 Extracting {first.name}\n"
                f"Progress: {pct}{'%' if str(pct).isdigit() else ''}\n"
                f"Free disk: {human_size(free)}",
            )
            last_edit = now
    code = await proc.wait()
    return code, tail, first


@app.on_message(filters.command("test7z"))
@admin_only
async def test7z_handler(client: Client, message: Message):
    if operation_lock.locked():
        return await message.reply_text("⚠️ Archive operation already running.")
    status = await message.reply_text("🧪 Testing split archive ...")
    async with operation_lock:
        code, out, first = await _test_archive()
    await edit_safe(
        status,
        f"{'✅' if code == 0 else '❌'} 7z test {first.name} exit={code}\n{out[-3500:]}"
    )


@app.on_message(filters.command("info7z"))
@admin_only
async def info7z_handler(client: Client, message: Message):
    _, items = choose_group(True)
    first = items[0][1]
    code, out = await run_cmd([SEVEN_ZIP, "l", "-slt", str(first)])
    await message.reply_text(f"exit={code}\n{out[-3500:]}")


@app.on_message(filters.command("extract7z"))
@admin_only
async def extract7z_handler(client: Client, message: Message):
    if len(message.command or []) < 2 or message.command[1] != "CONFIRM":
        return await message.reply_text("Use: /extract7z CONFIRM")
    if operation_lock.locked():
        return await message.reply_text("⚠️ Archive operation already running.")
    status = await message.reply_text("📦 Starting archive extraction ...")
    async with operation_lock:
        code, tail, first = await _extract_archive(status)
    await edit_safe(
        status,
        f"{'✅' if code == 0 else '❌'} Extraction finished: {first.name} exit={code}\n"
        f"Source ZIP: {'found' if ZIP_PATH.exists() else 'not found'}\n"
        f"Free disk: {human_size(shutil.disk_usage(EXTRACT_DIR).free)}",
    )
    if code != 0:
        await message.reply_text(tail[-3500:])


# ---------------- ONE-BOT pipeline/import/API controls ----------------

@app.on_message(filters.command(["workflow", "pipeline_status"]))
@admin_only
async def workflow_handler(client: Client, message: Message):
    await message.reply_text(_workflow_text())


@app.on_message(filters.command("import_start"))
@admin_only
async def import_start_handler(client: Client, message: Message):
    try:
        state, pid = await asyncio.to_thread(_start_import)
        if state == "complete":
            await message.reply_text(
                "✅ Import + indexes already complete. Use /api_start or /buildall CONFIRM."
            )
            return
        _ensure_pipeline_monitor()
        await message.reply_text(
            f"✅ Import {state}. PID={pid}\n"
            "It runs independently in background.\n"
            "Use /import_status."
        )
    except Exception as e:
        await message.reply_text(f"❌ Import start failed: {type(e).__name__}: {e}")


@app.on_message(filters.command("import_status"))
@admin_only
async def import_status_handler(client: Client, message: Message):
    await message.reply_text(
        _workflow_text() + "\n\nImport log:\n" + _tail(IMPORT_LOG, 15, 2200)
    )


@app.on_message(filters.command("import_stop"))
@admin_only
async def import_stop_handler(client: Client, message: Message):
    if len(message.command or []) < 2 or message.command[1] != "CONFIRM":
        return await message.reply_text("Use: /import_stop CONFIRM")
    global pipeline_task
    stopped, pid = await asyncio.to_thread(_stop_detached, IMPORT_PID_FILE)
    if pipeline_task and not pipeline_task.done():
        pipeline_task.cancel()
        pipeline_task = None
    await message.reply_text(
        ("✅ Import stopped" if stopped else "ℹ️ Import not running")
        + (f" (PID {pid})" if pid else "")
        + "\nCheckpoint is preserved; /import_start resumes safely."
    )


@app.on_message(filters.command("api_start"))
@admin_only
async def api_start_handler(client: Client, message: Message):
    try:
        state, pid = _start_api()
        await message.reply_text(
            f"✅ API {state}. PID={pid}\n{_api_urls_text()}"
        )
    except Exception as e:
        await message.reply_text(f"❌ API start failed: {type(e).__name__}: {e}")


@app.on_message(filters.command("api_status"))
@admin_only
async def api_status_handler(client: Client, message: Message):
    alive, pid = _proc_status(API_PID_FILE)
    await message.reply_text(
        ("API: 🟢 managed running" if alive else "API: 🟢 external running" if await asyncio.to_thread(_api_health_ok) else "API: ⚪ stopped")
        + (f" PID={pid}" if pid else "")
        + "\n" + _api_urls_text()
        + f"\nLookup ready: {_lookup_ready()}"
        + "\n\nAPI log:\n"
        + _tail(API_LOG, 12, 1800)
    )


@app.on_message(filters.command("api_stop"))
@admin_only
async def api_stop_handler(client: Client, message: Message):
    if len(message.command or []) < 2 or message.command[1] != "CONFIRM":
        return await message.reply_text("Use: /api_stop CONFIRM")
    stopped, pid = await asyncio.to_thread(_stop_detached, API_PID_FILE)
    await message.reply_text(
        ("✅ API stopped" if stopped else "ℹ️ API not running")
        + (f" (PID {pid})" if pid else "")
    )



@app.on_message(filters.command(["public_url", "network"]))
@admin_only
async def public_url_handler(client: Client, message: Message):
    await message.reply_text(
        "🌐 API network\n"
        + _api_urls_text()
        + "\n\nRemote request requires a valid X-API-Key."
    )


@app.on_message(filters.command("set_public_url"))
@admin_only
async def set_public_url_handler(client: Client, message: Message):
    """Update the displayed HTTPS public hostname without restarting API/tunnel."""
    global PUBLIC_API_URL
    args = message.command or []
    if len(args) != 2:
        return await message.reply_text(
            "Use: /set_public_url https://dbapi.teamsecret.in\n"
            "This only changes the bot's displayed URL, not DNS or tunnel routing."
        )
    value = args[1].strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        labels = host.split(".")
        valid_host = (
            1 < len(labels) and len(host) <= 253 and
            all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in labels)
        )
        valid = (
            parsed.scheme == "https" and parsed.netloc.lower() == host.lower() and
            not parsed.path and not parsed.query and not parsed.fragment and
            valid_host
        )
    except ValueError:
        valid = False
    if not valid:
        return await message.reply_text(
            "Invalid URL. Send a plain HTTPS hostname, for example:\n"
            "/set_public_url https://dbapi.teamsecret.in"
        )
    try:
        await asyncio.to_thread(_set_env_value, "PUBLIC_API_URL", value)
    except OSError as exc:
        return await message.reply_text(f"Could not save public URL: {exc}")
    PUBLIC_API_URL = value
    await message.reply_text(
        f"✅ Public API URL updated: {PUBLIC_API_URL}\n"
        "Updated immediately and saved in .env for next bot restart.\n"
        "Cloudflare Tunnel/DNS must be running separately."
    )


@app.on_message(filters.command("public_on"))
@admin_only
async def public_on_handler(client: Client, message: Message):
    global API_HOST
    if len(message.command or []) < 2 or message.command[1] != "CONFIRM":
        return await message.reply_text(
            "Use: /public_on CONFIRM\n"
            "This binds the API to 0.0.0.0 so it can be reached via server IP:port."
        )
    API_HOST = "0.0.0.0"
    _set_env_value("API_HOST", API_HOST)
    try:
        state, pid = await asyncio.to_thread(_restart_api_if_running)
        await message.reply_text(
            "✅ Public IP:port mode enabled.\n"
            + _api_urls_text()
            + (f"\nAPI {state}" + (f" PID={pid}" if pid else "") if state else "")
            + "\n\nAll lookup endpoints still require X-API-Key."
        )
    except Exception as e:
        await message.reply_text(
            f"✅ Public mode saved, but API restart failed: {type(e).__name__}: {e}\n"
            + _api_urls_text()
        )


@app.on_message(filters.command("public_off"))
@admin_only
async def public_off_handler(client: Client, message: Message):
    global API_HOST
    if len(message.command or []) < 2 or message.command[1] != "CONFIRM":
        return await message.reply_text("Use: /public_off CONFIRM")
    API_HOST = "127.0.0.1"
    _set_env_value("API_HOST", API_HOST)
    try:
        state, pid = await asyncio.to_thread(_restart_api_if_running)
        await message.reply_text(
            "✅ Public access disabled; API is localhost-only.\n"
            + _api_urls_text()
        )
    except Exception as e:
        await message.reply_text(f"⚠️ Saved localhost mode but restart failed: {e}")


@app.on_message(filters.command("logs"))
@admin_only
async def logs_handler(client: Client, message: Message):
    args = (message.command or [])[1:]
    if not args or args[0].lower() not in {"import", "api"}:
        return await message.reply_text("Use: /logs import [lines] OR /logs api [lines]")
    lines = 25
    if len(args) > 1 and args[1].isdigit():
        lines = max(5, min(80, int(args[1])))
    path = IMPORT_LOG if args[0].lower() == "import" else API_LOG
    await message.reply_text(
        f"{args[0].upper()} log:\n{_tail(path, lines, 3800)}"
    )


@app.on_message(filters.command(["buildall", "setupall"]))
@admin_only
async def buildall_handler(client: Client, message: Message):
    if len(message.command or []) < 2 or message.command[1] != "CONFIRM":
        return await message.reply_text(
            "Use: /buildall CONFIRM\n\n"
            "It will verify/extract if needed, start/resume JSON import, build indexes, "
            "then auto-start the local API."
        )

    global upload_mode
    upload_mode = False
    status = await message.reply_text(
        "🚀 Build-all started. Upload mode turned OFF. Checking current state..."
    )
    try:
        # Check completed DB first: source archive may already be deleted.
        if _lookup_ready():
            state, pid = _start_api()
            await edit_safe(status, f"✅ Database ready. API {state} PID={pid}.")
            return
        # Existing import checkpoint in indexing phase must be recovered,
        # never re-extracted/re-imported simply because the ZIP is missing.
        index_resume = _lookup_meta().get("phase") == "indexing" and LOOKUP_DB.exists()
        if LOOKUP_DB.exists() and ops.matches('recover_indexes_lowram.py'):
            index_resume = True
        if not ZIP_PATH.exists() and not index_resume:
            if operation_lock.locked():
                raise RuntimeError("archive operation already running")
            await edit_safe(status, "🧪 Source ZIP missing. Testing split archive first...")
            async with operation_lock:
                code, out, first = await _test_archive()
                if code != 0:
                    raise RuntimeError(
                        f"7z test failed for {first.name}: {out[-1200:]}"
                    )
                await edit_safe(
                    status,
                    f"✅ Archive valid: {first.name}\n📦 Extracting to {EXTRACT_DIR}..."
                )
                code, tail, first = await _extract_archive(status)
                if code != 0:
                    raise RuntimeError(f"7z extraction failed: {tail[-1200:]}")
            if not ZIP_PATH.exists():
                raise RuntimeError(
                    f"extraction finished but {ZIP_PATH.name} was not found"
                )

        if _lookup_ready():
            state, pid = _start_api()
            await edit_safe(
                status,
                f"✅ Everything already built. API {state} PID={pid}.\n"
                "Use /lookup <number>."
            )
            return

        state, pid = await asyncio.to_thread(_start_import)
        _ensure_pipeline_monitor()
        await edit_safe(
            status,
            f"✅ {'Existing DB indexing checkpoint' if index_resume else 'Source ZIP ready: ' + human_size(ZIP_PATH.stat().st_size)}\n"
            f"🔄 Import/index recovery {state} PID={pid}. It runs in background even if RDP disconnects.\n"
            "Indexes use low-RAM mode automatically. API will auto-start when ready.\n"
            "Use /workflow or /import_status anytime."
        )
    except Exception as e:
        log.exception("buildall failed")
        await edit_safe(
            status,
            f"❌ Build-all failed: {type(e).__name__}: {e}\n"
            "Use /workflow and /logs import 30."
        )


async def _watch_index_completion():
    """Notify admins when an existing standalone index recovery exits."""
    seen = bool(ops.matches('recover_indexes_lowram.py'))
    if not seen:
        return
    while ops.matches('recover_indexes_lowram.py'):
        await asyncio.sleep(20)
    # Log may be delayed briefly after process exit.
    await asyncio.sleep(2)
    if _lookup_ready():
        await _notify_admins('✅ Index recovery completed. Database ready. /index_status')
    else:
        await _notify_admins('⚠️ Index process exited but DB is not ready. /index_logs')


def _ensure_index_watch():
    global index_watch_task
    if index_watch_task is None or index_watch_task.done():
        index_watch_task = asyncio.create_task(_watch_index_completion(), name='index-watch')


# ---------------- Admin operational control ----------------

@app.on_message(filters.command("control"))
@admin_only
async def control_handler(client: Client, message: Message):
    await message.reply_text(
        "ADMIN CONTROL PANEL\n"
        "/workflow /parts /disk /upload_on /upload_off\n"
        "/test7z /extract7z CONFIRM /buildall CONFIRM\n"
        "/import_status /index_status /index_start CONFIRM /index_logs\n"
        "/api_start /api_status /api_restart /api_stop CONFIRM\n"
        "/public_url /set_public_url https://your.domain\n"
        "/tunnel_status /tunnel_start /tunnel_restart /tunnel_stop CONFIRM /tunnel_logs\n"
        "/server_status /io /bot_logs /api_logs\n"
        "/cleanup (dry-run only) /cleanup_zip CONFIRM\n"
        "/genkey /keys /disable /enable /deletekey /lookup /id"
    )


@app.on_message(filters.command("index_status"))
@admin_only
async def index_status_handler(client: Client, message: Message):
    await message.reply_text(await asyncio.to_thread(
        ops.index_status, LOOKUP_DB, IMPORT_LOG, _tail))


@app.on_message(filters.command("index_logs"))
@admin_only
async def index_logs_handler(client: Client, message: Message):
    await message.reply_text(
        "Index recovery log:\n" + _tail(ops.INDEX_LOG, 18, 1800)
        + "\n\nImport handoff log:\n" + _tail(IMPORT_LOG, 12, 1500))


@app.on_message(filters.command("index_start"))
@admin_only
async def index_start_handler(client: Client, message: Message):
    if _args(message) != ["CONFIRM"]:
        return await message.reply_text("Use /index_start CONFIRM (only if import completed and indexing failed)")
    if _lookup_ready():
        return await message.reply_text("✅ Database already ready. No indexing needed.")
    import_alive, _ = _proc_status(IMPORT_PID_FILE)
    if import_alive:
        return await message.reply_text("⚠️ Import/index worker is already running.")
    if ops.matches('recover_indexes_lowram.py'):
        return await message.reply_text("✅ Index recovery already running. Use /index_status.")
    if not LOOKUP_DB.exists():
        return await message.reply_text("Database missing; cannot recover indexes.")
    meta = _lookup_meta()
    if meta.get('phase') != 'indexing':
        return await message.reply_text(f"Refusing recovery: database phase={meta.get('phase')!r}, expected 'indexing'.")
    if ops.matches('recover_indexes_lowram.py'):
        return await message.reply_text("Index recovery already running.")
    py = ROOT / 'venv' / 'bin' / 'python3'
    if not py.exists():
        return await message.reply_text("Missing venv Python.")
    try:
        state, pid = await asyncio.to_thread(_start_index_recovery)
        _ensure_index_watch()
        _ensure_pipeline_monitor()
        await message.reply_text(f"Index recovery {state}; PID={pid}. Use /index_status.")
    except Exception as e:
        await message.reply_text(f"Recovery start failed: {type(e).__name__}: {e}")


@app.on_message(filters.command("api_restart"))
@admin_only
async def api_restart_handler(client: Client, message: Message):
    # Only restart an instance that has been started and recorded by this bot.
    alive, pid = _proc_status(API_PID_FILE)
    if alive:
        await asyncio.to_thread(_stop_detached, API_PID_FILE)
    try:
        state, pid = await asyncio.to_thread(_start_api)
        await message.reply_text(f"API {state}, PID={pid}. If another unmanaged API owns port {API_PORT}, see /api_logs.")
    except Exception as e:
        await message.reply_text(f"API restart failed: {e}")


@app.on_message(filters.command("tunnel_start"))
@admin_only
async def tunnel_start_handler(client: Client, message: Message):
    if not ops.TUNNEL_CONFIG.is_file():
        return await message.reply_text(f"Tunnel config missing: {ops.TUNNEL_CONFIG}")
    try:
        state, pid = await asyncio.to_thread(ops.start, 'tunnel',
            [ops.CLOUDFLARED, 'tunnel', '--config', str(ops.TUNNEL_CONFIG), 'run'],
            ROOT/'tunnel.log', str(ops.TUNNEL_CONFIG))
        await message.reply_text(f"Cloudflare tunnel {state}; PID={pid}. Use /tunnel_logs")
    except Exception as e:
        await message.reply_text(f"Tunnel start failed: {e}")


@app.on_message(filters.command("tunnel_status"))
@admin_only
async def tunnel_status_handler(client: Client, message: Message):
    pids = ops.matches(str(ops.TUNNEL_CONFIG))
    await message.reply_text(f"Cloudflare tunnel: {'running' if pids else 'stopped'}\n"
        f"PIDs: {pids or '-'}\nConfig: {ops.TUNNEL_CONFIG}\n"
        f"Managed PID: {ops.owned_pid('tunnel') or '-'}\n"
        f"Latest log:\n{_tail(ROOT/'tunnel.log', 8, 1100)}")


@app.on_message(filters.command("tunnel_stop"))
@admin_only
async def tunnel_stop_handler(client: Client, message: Message):
    if _args(message) != ['CONFIRM']:
        return await message.reply_text('Use /tunnel_stop CONFIRM')
    try:
        pid = await asyncio.to_thread(ops.stop, 'tunnel')
        await message.reply_text(f"Stopped managed tunnel PID={pid}.")
    except Exception as e:
        await message.reply_text(str(e) + '\nExternal screen tunnel must be stopped outside the bot once to migrate control.')


@app.on_message(filters.command("tunnel_restart"))
@admin_only
async def tunnel_restart_handler(client: Client, message: Message):
    pid = ops.owned_pid('tunnel')
    if pid:
        await asyncio.to_thread(ops.stop, 'tunnel')
    elif ops.matches(str(ops.TUNNEL_CONFIG)):
        return await message.reply_text('Tunnel was started outside bot; refusing to kill it. /tunnel_status')
    await tunnel_start_handler.__wrapped__(client, message)


@app.on_message(filters.command('tunnel_logs'))
@admin_only
async def tunnel_logs_handler(client: Client, message: Message):
    await message.reply_text(_tail(ROOT/'tunnel.log', 25, 3500) + '\n\nExternal screen log: ' + _tail(Path('/root/db2-tunnel.log'), 12, 1300))


@app.on_message(filters.command(['server_status', 'ram']))
@admin_only
async def server_status_handler(client: Client, message: Message):
    disk = shutil.disk_usage(ROOT)
    with open('/proc/meminfo') as f:
        mem = {k: int(v.split()[0])*1024 for k, v in (line.split(':',1) for line in f)}
    limit = Path('/sys/fs/cgroup/memory.max')
    cap = limit.read_text().strip() if limit.exists() else 'unknown'
    await message.reply_text(
        f"Disk: total {human_size(disk.total)} / free {human_size(disk.free)}\n"
        f"Host RAM available: {human_size(mem.get('MemAvailable', 0))}\n"
        f"Container RAM cap: {human_size(int(cap)) if cap.isdigit() else cap}\n"
        f"DB size: {human_size(LOOKUP_DB.stat().st_size) if LOOKUP_DB.exists() else 'missing'}\n"
        f"API PID: {_proc_status(API_PID_FILE)[1] or 'unmanaged/stopped'}\n"
        f"Tunnel PIDs: {ops.matches(str(ops.TUNNEL_CONFIG)) or '-'}")


@app.on_message(filters.command('io'))
@admin_only
async def io_handler(client: Client, message: Message):
    pids = ops.matches('recover_indexes_lowram.py') or ops.matches('import_users.py')
    if not pids:
        return await message.reply_text('No active importer/indexer process found.')
    pid = pids[0]
    try:
        a = await asyncio.to_thread(ops.process_io, pid)
        await asyncio.sleep(2)
        b = await asyncio.to_thread(ops.process_io, pid)
        await message.reply_text(f"PID {pid} disk I/O\n"
            f"Read {(b['read_bytes']-a['read_bytes'])/2/1048576:.1f} MiB/s\n"
            f"Write {(b['write_bytes']-a['write_bytes'])/2/1048576:.1f} MiB/s")
    except Exception as e:
        await message.reply_text(f"I/O unavailable: {e}")


@app.on_message(filters.command('bot_logs'))
@admin_only
async def bot_logs_handler(client: Client, message: Message):
    await message.reply_text(_tail(ROOT/'bot.log', 25, 3500))


@app.on_message(filters.command('api_logs'))
@admin_only
async def api_logs_handler(client: Client, message: Message):
    await message.reply_text(_tail(API_LOG, 25, 3500) + '\n\nExternal log:\n' + _tail(Path('/root/api.log'), 12, 1100))


@app.on_message(filters.command('cleanup'))
@admin_only
async def cleanup_handler(client: Client, message: Message):
    if ZIP_PATH.exists():
        await message.reply_text(f"Cleanup dry run only. ZIP: {ZIP_PATH} ({human_size(ZIP_PATH.stat().st_size)})\n"
            "Only use /cleanup_zip CONFIRM if backup has been verified on another disk.")
    else:
        await message.reply_text('Source ZIP already absent. Database is NOT a cleanup target.')


@app.on_message(filters.command('cleanup_zip'))
@admin_only
async def cleanup_zip_handler(client: Client, message: Message):
    if _args(message) != ['CONFIRM']:
        return await message.reply_text('Use /cleanup_zip CONFIRM after verifying remote backup.')
    if not _lookup_ready():
        return await message.reply_text('Refused: database is not marked ready.')
    if _proc_status(IMPORT_PID_FILE)[0] or ops.matches('recover_indexes_lowram.py'):
        return await message.reply_text('Refused: import/index process still running.')
    if ZIP_PATH.exists():
        size = ZIP_PATH.stat().st_size
        ZIP_PATH.unlink()
        await message.reply_text(f'Deleted only source ZIP ({human_size(size)}). SQLite DB untouched.')
    else:
        await message.reply_text('ZIP already absent.')


# ---------------- API key management + direct lookup ----------------
# ---------------- API key management + direct lookup ----------------

def _args(message: Message):
    command = message.command or []
    return command[1:]


def _lookup_conn():
    if not LOOKUP_DB.exists():
        raise FileNotFoundError(f"Lookup DB not ready: {LOOKUP_DB}")
    con = sqlite3.connect(f"file:{LOOKUP_DB}?mode=ro", uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def _normalize_number(raw: str):
    s = re.sub(r"\D+", "", raw or "")
    if not (7 <= len(s) <= 15):
        raise ValueError("number must contain 7-15 digits")
    return s


def _fmt_record(row):
    d = dict(row)
    return (
        f"_id: {d.get('mongo_oid') or '-'}\n"
        f"Mobile: {d.get('mobile') or '-'}\n"
        f"Name: {d.get('name') or '-'}\n"
        f"Father: {d.get('fname') or '-'}\n"
        f"Address: {d.get('address') or '-'}\n"
        f"Alt: {d.get('alt') or '-'}\n"
        f"Circle: {d.get('circle') or '-'}\n"
        f"ID: {d.get('ext_id') or '-'}\n"
        f"Email: {d.get('email') or '-'}"
    )


@app.on_message(filters.command("genkey"))
@admin_only
async def genkey_handler(client: Client, message: Message):
    args = _args(message)
    if not args:
        return await message.reply_text("Use: /genkey <name> [days|never]")
    days = None
    if len(args) >= 2 and (args[-1].lower() == "never" or args[-1].isdigit()):
        last = args.pop()
        if last.lower() != "never":
            days = max(1, min(3650, int(last)))
    name = " ".join(args).strip()
    if not name:
        return await message.reply_text("Key name required.")
    data = key_store.generate_key(KEYS_DB, name, message.from_user.id, days)
    await message.reply_text(
        "✅ API key generated\n\n"
        f"ID: {data['id']}\nName: {data['name']}\n"
        f"Expires: {data['expires_at'] or 'Never'}\n"
        f"Key: {data['key']}\n\nSave it now. Raw key is not stored."
    )


@app.on_message(filters.command("keys"))
@admin_only
async def keys_handler(client: Client, message: Message):
    rows = key_store.list_keys(KEYS_DB)
    if not rows:
        return await message.reply_text("No API keys.")
    lines = ["🔐 API keys"]
    for r in rows[:100]:
        state = "🟢" if r['enabled'] else "🔴"
        lines.append(f"{state} #{r['id']} {r['name']} | {r['key_prefix']}… | uses={r['use_count']} | exp={r.get('expires_at') or 'Never'}")
    await message.reply_text("\n".join(lines))


@app.on_message(filters.command("key"))
@admin_only
async def key_info_handler(client: Client, message: Message):
    args = _args(message)
    if not args or not args[0].isdigit():
        return await message.reply_text("Use: /key <id>")
    r = key_store.get_key(KEYS_DB, int(args[0]))
    if not r:
        return await message.reply_text("Key not found.")
    await message.reply_text(
        f"Key #{r['id']}\nName: {r['name']}\nEnabled: {bool(r['enabled'])}\n"
        f"Prefix: {r['key_prefix']}…\nCreated: {r['created_at']}\n"
        f"Expires: {r.get('expires_at') or 'Never'}\nLast used: {r.get('last_used_at') or 'Never'}\nUses: {r['use_count']}"
    )


async def _toggle_key(message: Message, enabled: bool):
    args = _args(message)
    if not args or not args[0].isdigit():
        return await message.reply_text(f"Use: /{'enable' if enabled else 'disable'} <id>")
    ok = key_store.set_enabled(KEYS_DB, int(args[0]), enabled)
    await message.reply_text("✅ Updated." if ok else "❌ Key not found.")


@app.on_message(filters.command("disable"))
@admin_only
async def disable_key_handler(client: Client, message: Message):
    await _toggle_key(message, False)


@app.on_message(filters.command("enable"))
@admin_only
async def enable_key_handler(client: Client, message: Message):
    await _toggle_key(message, True)


@app.on_message(filters.command("rename"))
@admin_only
async def rename_key_handler(client: Client, message: Message):
    args = _args(message)
    if len(args) < 2 or not args[0].isdigit():
        return await message.reply_text("Use: /rename <id> <new name>")
    ok = key_store.rename_key(KEYS_DB, int(args[0]), " ".join(args[1:]).strip())
    await message.reply_text("✅ Renamed." if ok else "❌ Key not found.")


@app.on_message(filters.command("expire"))
@admin_only
async def expire_key_handler(client: Client, message: Message):
    args = _args(message)
    if len(args) != 2 or not args[0].isdigit() or not (args[1].isdigit() or args[1].lower() == "never"):
        return await message.reply_text("Use: /expire <id> <days|never>")
    days = None if args[1].lower() == "never" else max(1, min(3650, int(args[1])))
    ok, exp = key_store.set_expiry(KEYS_DB, int(args[0]), days)
    await message.reply_text(f"✅ Expires: {exp or 'Never'}" if ok else "❌ Key not found.")


@app.on_message(filters.command("deletekey"))
@admin_only
async def delete_key_handler(client: Client, message: Message):
    args = _args(message)
    if len(args) < 2 or not args[0].isdigit() or args[1] != "CONFIRM":
        return await message.reply_text("Use: /deletekey <id> CONFIRM")
    ok = key_store.delete_key(KEYS_DB, int(args[0]))
    await message.reply_text("✅ Key deleted." if ok else "❌ Key not found.")


@app.on_message(filters.command("lookup"))
@admin_only
async def lookup_handler(client: Client, message: Message):
    args = _args(message)
    if not args:
        return await message.reply_text("Use: /lookup <number>")
    if not _lookup_ready():
        return await message.reply_text("⏳ Lookup DB is not ready yet. Use /workflow or /buildall CONFIRM.")
    try:
        n = _normalize_number(args[0])
        with _lookup_conn() as con:
            rows = con.execute(
                "SELECT mongo_oid,mobile,name,fname,address,alt,circle,ext_id,email FROM users WHERE mobile=? OR alt=? LIMIT ?",
                (n, n, MAX_LOOKUP_RESULTS),
            ).fetchall()
    except Exception as e:
        return await message.reply_text(f"❌ Lookup unavailable: {e}")
    if not rows:
        return await message.reply_text("No record found.")
    text = f"Found {len(rows)} record(s) for {n}\n" + "\n\n".join(f"#{i}\n{_fmt_record(r)}" for i, r in enumerate(rows, 1))
    for pos in range(0, len(text), 3900):
        await message.reply_text(text[pos:pos+3900])


@app.on_message(filters.command("id"))
@admin_only
async def id_lookup_handler(client: Client, message: Message):
    args = _args(message)
    if not args:
        return await message.reply_text("Use: /id <record_id>")
    if not _lookup_ready():
        return await message.reply_text("⏳ Lookup DB is not ready yet. Use /workflow or /buildall CONFIRM.")
    try:
        with _lookup_conn() as con:
            rows = con.execute(
                "SELECT mongo_oid,mobile,name,fname,address,alt,circle,ext_id,email FROM users WHERE ext_id=? LIMIT ?",
                (args[0].strip(), MAX_LOOKUP_RESULTS),
            ).fetchall()
    except Exception as e:
        return await message.reply_text(f"❌ Lookup unavailable: {e}")
    if not rows:
        return await message.reply_text("No record found.")
    text = "\n\n".join(f"#{i}\n{_fmt_record(r)}" for i, r in enumerate(rows, 1))
    for pos in range(0, len(text), 3900):
        await message.reply_text(text[pos:pos+3900])


@app.on_message(filters.command("stats"))
@admin_only
async def stats_handler(client: Client, message: Message):
    if not LOOKUP_DB.exists():
        return await message.reply_text(f"Lookup DB not ready: {LOOKUP_DB}")
    try:
        with _lookup_conn() as con:
            rows = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            meta = {r[0]: r[1] for r in con.execute("SELECT key,value FROM import_meta").fetchall()}
        await message.reply_text(
            f"Lookup DB: {LOOKUP_DB}\nSize: {human_size(LOOKUP_DB.stat().st_size)}\nRows: {rows:,}\n"
            f"Import complete: {meta.get('completed','0')}\nProcessed source: {meta.get('processed_records','0')}"
        )
    except Exception as e:
        await message.reply_text(f"❌ Stats error: {e}")

# Document handler comes AFTER command handlers and is private/admin only.
@app.on_message(filters.document, group=10)
@admin_only
async def receive_document(client: Client, message: Message):
    global upload_mode
    doc = message.document
    name = safe_filename(doc.file_name or f"telegram_{message.id}.bin")
    size = int(doc.file_size or 0)

    if not upload_mode:
        return await message.reply_text(
            f"⛔ Upload mode OFF. {name} was NOT queued.\nUse /upload_on first."
        )

    job_id, inserted = add_job(message.chat.id, message.id, name, size, doc.file_id)
    if not inserted:
        with db_conn() as con:
            row = con.execute("SELECT status FROM queue WHERE id=?", (job_id,)).fetchone()
        return await message.reply_text(f"ℹ️ Already known: {name} • status={row['status']}")

    position = waiting_ahead(job_id) + (1 if current_job_id else 0) + 1
    await message.reply_text(
        f"📥 Queued {name}\n"
        f"Size: {human_size(size)}\n"
        f"Queue position: {position}"
    )


def _segment_plan(total_size: int, streams: int):
    """Return contiguous 1-MiB aligned ranges for parallel Telegram downloads."""
    if total_size <= 0:
        return []
    total_chunks = (total_size + TG_CHUNK_SIZE - 1) // TG_CHUNK_SIZE
    workers = max(1, min(int(streams), int(total_chunks)))
    base, extra = divmod(total_chunks, workers)
    plan = []
    start_chunk = 0
    for idx in range(workers):
        chunk_count = base + (1 if idx < extra else 0)
        end_chunk = start_chunk + chunk_count
        start_byte = start_chunk * TG_CHUNK_SIZE
        end_byte = min(total_size, end_chunk * TG_CHUNK_SIZE)
        plan.append({
            "index": idx,
            "start_chunk": start_chunk,
            "end_chunk": end_chunk,
            "size": end_byte - start_byte,
        })
        start_chunk = end_chunk
    return plan


def _segment_path(temp: Path, index: int) -> Path:
    return Path(f"{temp}.seg{index:02d}")


def _cleanup_segments(temp: Path):
    for p in temp.parent.glob(temp.name + ".seg*"):
        with suppress(FileNotFoundError):
            p.unlink()


def _prepare_resume_segment(path: Path, expected_size: int):
    """Keep only complete 1-MiB chunks so resuming never duplicates bytes."""
    if not path.exists():
        return 0
    size = path.stat().st_size
    if size > expected_size:
        with path.open("r+b") as fh:
            fh.truncate(expected_size)
        size = expected_size
    if size == expected_size:
        return size
    aligned = (size // TG_CHUNK_SIZE) * TG_CHUNK_SIZE
    if aligned != size:
        with path.open("r+b") as fh:
            fh.truncate(aligned)
    return aligned


def _merge_segments_sync(temp: Path, segment_paths, expected_size: int):
    """Assemble segment files without loading the whole Telegram file into RAM."""
    merge_tmp = Path(str(temp) + ".assembling")
    with suppress(FileNotFoundError):
        merge_tmp.unlink()
    with merge_tmp.open("wb", buffering=8 * 1024 * 1024) as out:
        for seg in segment_paths:
            with seg.open("rb", buffering=8 * 1024 * 1024) as src:
                shutil.copyfileobj(src, out, length=8 * 1024 * 1024)
        out.flush()
        os.fsync(out.fileno())
    got = merge_tmp.stat().st_size
    if expected_size and got != expected_size:
        raise RuntimeError(f"Assembled size mismatch: got {got}, expected {expected_size}")
    os.replace(merge_tmp, temp)


async def parallel_download_file(media_file_id: str, expected: int, temp: Path, status: Message, job):
    """
    Download ONE Telegram file using several independent 1-MiB-aligned ranges.
    The queue still processes only one file at a time. Each range is persisted
    in its own segment file, so a restart resumes from completed MiB chunks.
    """
    if not media_file_id:
        raise RuntimeError("Telegram file_id is missing; cannot start ranged download")
    if expected <= 0:
        raise RuntimeError("Telegram file size is unknown; cannot split ranged download safely")

    decoded = FileId.decode(media_file_id)
    plan = _segment_plan(expected, PARALLEL_STREAMS)
    if not plan:
        raise RuntimeError("Could not build parallel download plan")

    segment_done = {}
    segment_paths = []
    for seg in plan:
        path = _segment_path(temp, seg["index"])
        have = _prepare_resume_segment(path, seg["size"])
        segment_done[seg["index"]] = have
        segment_paths.append(path)

    resumed_bytes = sum(segment_done.values())
    transfer_started = time.monotonic()
    state = {
        "last_t": transfer_started,
        "last_bytes": resumed_bytes,
        "smoothed": 0.0,
    }

    async def range_worker(seg):
        idx = seg["index"]
        path = _segment_path(temp, idx)
        if segment_done[idx] >= seg["size"]:
            return

        # PyroFork's get_file() can occasionally end a ranged generator early
        # after a media-DC timeout/reconnect. Treat that as resumable, not fatal.
        # Every retry starts at the next complete 1 MiB chunk already persisted.
        attempts = 0
        last_error = None
        while segment_done[idx] < seg["size"]:
            if cancel_current:
                raise asyncio.CancelledError("cancel requested")

            # Never trust a partial Telegram chunk across a reconnect. Keep the
            # segment at a complete 1 MiB boundary (unless it is fully complete).
            current_size = path.stat().st_size if path.exists() else 0
            if current_size != segment_done[idx]:
                segment_done[idx] = current_size
            if segment_done[idx] < seg["size"]:
                aligned = (segment_done[idx] // TG_CHUNK_SIZE) * TG_CHUNK_SIZE
                if aligned != segment_done[idx]:
                    with path.open("r+b") as trim:
                        trim.truncate(aligned)
                    segment_done[idx] = aligned

            completed_chunks = segment_done[idx] // TG_CHUNK_SIZE
            offset_chunks = seg["start_chunk"] + completed_chunks
            remaining_chunks = seg["end_chunk"] - offset_chunks
            if remaining_chunks <= 0:
                break

            before = segment_done[idx]
            try:
                with path.open("ab", buffering=2 * TG_CHUNK_SIZE) as fh:
                    async for chunk in app.get_file(
                        decoded,
                        file_size=expected,
                        limit=remaining_chunks,
                        offset=offset_chunks,
                    ):
                        if cancel_current:
                            raise asyncio.CancelledError("cancel requested")
                        remaining_bytes = seg["size"] - segment_done[idx]
                        if remaining_bytes <= 0:
                            break
                        if len(chunk) > remaining_bytes:
                            chunk = chunk[:remaining_bytes]
                        fh.write(chunk)
                        segment_done[idx] += len(chunk)
                        if segment_done[idx] >= seg["size"]:
                            break
                    fh.flush()
                    os.fsync(fh.fileno())
                last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                log.warning(
                    "Range %s/%s interrupted at %s/%s: %s: %s",
                    idx + 1, len(plan), human_size(segment_done[idx]),
                    human_size(seg["size"]), type(exc).__name__, exc,
                )

            # A normal generator termination can still be an early EOF after a
            # timeout. If we made progress, reset the consecutive-failure budget.
            made_progress = segment_done[idx] > before
            if segment_done[idx] >= seg["size"]:
                break
            attempts = 0 if made_progress else attempts + 1
            if attempts >= STREAM_RETRIES:
                detail = (
                    f"; last error={type(last_error).__name__}: {last_error}"
                    if last_error else ""
                )
                raise RuntimeError(
                    f"Stream {idx + 1}/{len(plan)} stalled after {STREAM_RETRIES} retries: "
                    f"got {segment_done[idx]}, expected {seg['size']}{detail}"
                )

            wait = min(15.0, STREAM_RETRY_DELAY * (1.6 ** min(attempts, 5)))
            log.info(
                "Resuming range %s/%s from %s/%s in %.1fs",
                idx + 1, len(plan), human_size(segment_done[idx]),
                human_size(seg["size"]), wait,
            )
            await asyncio.sleep(wait)

        got = path.stat().st_size if path.exists() else 0
        if got != seg["size"]:
            raise RuntimeError(
                f"Stream {idx + 1}/{len(plan)} incomplete after retries: "
                f"got {got}, expected {seg['size']}"
            )

    tasks = [
        asyncio.create_task(range_worker(seg), name=f"tg-range-{seg['index']}")
        for seg in plan
    ]

    async def progress_reporter():
        while True:
            if all(t.done() for t in tasks):
                break
            await asyncio.sleep(PROGRESS_INTERVAL)
            now = time.monotonic()
            done = min(expected, sum(segment_done.values()))
            dt = max(now - state["last_t"], 0.001)
            delta = max(0, done - state["last_bytes"])
            inst = delta / dt
            if inst > 0:
                if state["smoothed"] <= 0:
                    state["smoothed"] = inst
                else:
                    state["smoothed"] = state["smoothed"] * 0.65 + inst * 0.35
            speed = state["smoothed"]
            eta = ((expected - done) / speed) if speed > 0 else None
            pct = done * 100 / expected
            counts = queue_counts()
            resumed_note = f" • resumed {human_size(resumed_bytes)}" if resumed_bytes else ""
            await edit_safe(
                status,
                f"⚡ Parallel {len(plan)}x: {job['file_name']}\n"
                f"[{progress_bar(done, expected)}] {pct:.1f}%\n"
                f"{human_size(done)} / {human_size(expected)}{resumed_note}\n"
                f"Speed: {human_size(speed)}/s • ETA: {fmt_eta(eta)}\n"
                f"Waiting: {counts.get('queued', 0)}",
            )
            state["last_t"] = now
            state["last_bytes"] = done

    reporter = asyncio.create_task(progress_reporter(), name="tg-progress-reporter")
    try:
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    finally:
        reporter.cancel()
        with suppress(asyncio.CancelledError):
            await reporter

    if cancel_current:
        raise asyncio.CancelledError("cancel requested")

    total_segments = sum(p.stat().st_size for p in segment_paths)
    if total_segments != expected:
        raise RuntimeError(f"Segment total mismatch: got {total_segments}, expected {expected}")

    await edit_safe(
        status,
        f"🧩 Download complete for {job['file_name']}\n"
        f"Assembling {len(segment_paths)} segments on disk...",
    )
    await asyncio.to_thread(_merge_segments_sync, temp, segment_paths, expected)
    return segment_paths, resumed_bytes


async def download_worker():
    global current_job_id, current_name, current_started, cancel_current

    while True:
        job = next_job()
        if not job:
            await asyncio.sleep(1.0)
            continue

        current_job_id = job["id"]
        current_name = job["file_name"]
        current_started = time.monotonic()
        cancel_current = False

        temp = UPLOAD_DIR / (job["file_name"] + ".downloading")
        final = UPLOAD_DIR / job["file_name"]
        status = None

        try:
            expected = int(job["file_size"] or 0)
            media_file_id = job.get("file_id")

            # Refresh the file_id/file_reference from the original message when
            # possible. This prevents FILE_REFERENCE_EXPIRED after long queues or
            # bot restarts. If the message is gone, the persisted file_id remains
            # a fallback.
            with suppress(Exception):
                msg = await app.get_messages(job["chat_id"], job["message_id"])
                if msg and msg.document:
                    media_file_id = msg.document.file_id
                    expected = int(msg.document.file_size or expected or 0)
                    with db_conn() as con:
                        con.execute(
                            "UPDATE queue SET file_id=?, file_size=?, updated_at=? WHERE id=?",
                            (media_file_id, expected, time.time(), job["id"]),
                        )

            if not media_file_id:
                raise RuntimeError("Telegram file_id is unavailable")
            if not expected:
                raise RuntimeError("Telegram file size is unavailable")

            # If already complete, don't download it again.
            if final.exists() and (not expected or final.stat().st_size == expected):
                set_job_status(job["id"], "done")
                _cleanup_segments(temp)
                with suppress(FileNotFoundError):
                    temp.unlink()
                with suppress(FileNotFoundError):
                    Path(str(temp) + ".assembling").unlink()
                with suppress(Exception):
                    await app.send_message(
                        job["chat_id"],
                        f"✅ Already exists, skipped: {final.name} ({human_size(final.stat().st_size)})",
                    )
                continue

            counts = queue_counts()
            status = await app.send_message(
                job["chat_id"],
                f"⚡ Starting {job['file_name']}\n"
                f"Size: {human_size(expected)}\n"
                f"Parallel streams: {PARALLEL_STREAMS} • auto-retry: {STREAM_RETRIES}\n"
                f"Only this ONE queue file is active. Waiting: {counts.get('queued', 0)}",
            )

            # A stale assembled temp can be recreated from persisted range segments.
            with suppress(FileNotFoundError):
                temp.unlink()
            with suppress(FileNotFoundError):
                Path(str(temp) + ".assembling").unlink()
            # Old single-stream Pyrogram versions could leave this behind.
            with suppress(FileNotFoundError):
                Path(str(temp) + ".temp").unlink()

            segment_paths, resumed_bytes = await parallel_download_file(
                media_file_id, expected, temp, status, job
            )

            if cancel_current:
                raise asyncio.CancelledError("cancel requested")
            if not temp.exists():
                raise RuntimeError("Assembled download file is missing")
            if expected and temp.stat().st_size != expected:
                raise RuntimeError(
                    f"Size mismatch: got {temp.stat().st_size}, expected {expected}"
                )

            os.replace(temp, final)
            # Only remove resumable segments after the final rename succeeds.
            for seg in segment_paths:
                with suppress(FileNotFoundError):
                    seg.unlink()

            elapsed = max(time.monotonic() - current_started, 0.001)
            transferred_this_run = max(0, final.stat().st_size - resumed_bytes)
            avg = transferred_this_run / elapsed
            set_job_status(job["id"], "done")
            counts3 = queue_counts()
            await edit_safe(
                status,
                f"✅ Downloaded {final.name}\n"
                f"Size: {human_size(final.stat().st_size)}\n"
                f"Parallel: {len(segment_paths)}x • This run avg: {human_size(avg)}/s\n"
                f"Resumed: {human_size(resumed_bytes)}\n"
                f"Waiting: {counts3.get('queued', 0)} • Done: {counts3.get('done', 0)}",
            )

        except asyncio.CancelledError:
            # KEEP segment files. The next attempt resumes at completed 1-MiB chunks.
            user_cancel = bool(cancel_current)
            set_job_status(
                job["id"],
                "queued",
                "cancelled and requeued; partial ranges preserved"
                if user_cancel else
                "bot stopped; requeued with partial ranges preserved",
            )
            if status and user_cancel:
                done = sum(
                    p.stat().st_size
                    for p in temp.parent.glob(temp.name + ".seg*")
                    if p.is_file()
                )
                await edit_safe(
                    status,
                    f"⏸ Cancelled {job['file_name']} and requeued.\n"
                    f"Saved partial data: {human_size(done)} — resume will continue from there.",
                )
            # A task cancellation caused by bot shutdown must not be swallowed,
            # otherwise app.stop() can hang waiting for this infinite worker.
            if not user_cancel:
                raise
        except Exception as e:
            log.exception("Download failed for job %s", job["id"])
            # KEEP segment files on failure so /retryfailed resumes instead of starting over.
            set_job_status(job["id"], "failed", f"{type(e).__name__}: {e}")
            done = sum(
                p.stat().st_size
                for p in temp.parent.glob(temp.name + ".seg*")
                if p.is_file()
            )
            text = (
                f"❌ Download failed {job['file_name']}\n"
                f"{type(e).__name__}: {e}\n"
                f"Partial preserved: {human_size(done)}\n"
                f"Use /retryfailed to resume."
            )
            if status:
                await edit_safe(status, text)
            else:
                with suppress(Exception):
                    await app.send_message(job["chat_id"], text)
        finally:
            current_job_id = None
            current_name = None
            current_started = None
            cancel_current = False



async def bot_main():
    global worker_task, pipeline_task, index_watch_task
    init_db()
    key_store.init_db(KEYS_DB)
    await app.start()
    me = await app.get_me()
    log.info("Started as @%s (id=%s) build=%s", me.username, me.id, BUILD)
    log.info("Pyrogram/PyroFork version active; .env=%s", ROOT / ".env")
    log.info("ADMIN_IDS=%s", sorted(ADMIN_IDS))
    log.info("Upload mode starts OFF; parallel streams/file=%s; stream retries=%s", PARALLEL_STREAMS, STREAM_RETRIES)

    worker_task = asyncio.create_task(download_worker(), name="download-worker")
    # Recover monitoring after bot restart while a detached import is still running.
    import_alive, _ = _proc_status(IMPORT_PID_FILE)
    if import_alive:
        _ensure_pipeline_monitor()
    existing_index = ops.matches('recover_indexes_lowram.py')
    if existing_index:
        _ensure_index_watch()
        _ensure_pipeline_monitor()
    elif not import_alive and _lookup_meta().get('phase') == 'indexing':
        # A previous importer may have been OOM-killed while the bot was down.
        # Do not touch the source archive; index-only resumption is guarded by a file lock.
        _ensure_pipeline_monitor()
    for uid in ADMIN_IDS:
        with suppress(Exception):
            await app.send_message(
                uid,
                f"✅ @{me.username} started with Pyrogram. Upload mode is OFF.\nBuild: {BUILD}\nParallel streams/file: {PARALLEL_STREAMS}\nSend /ping.",
            )
    try:
        await idle()
    finally:
        if worker_task:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
        if pipeline_task and not pipeline_task.done():
            pipeline_task.cancel()
            with suppress(asyncio.CancelledError):
                await pipeline_task
        if index_watch_task and not index_watch_task.done():
            index_watch_task.cancel()
            with suppress(asyncio.CancelledError):
                await index_watch_task
        await app.stop()


def main():
    # IMPORTANT: Pyrogram Client was constructed on its own event loop.
    # Use Client.run(coroutine), not asyncio.run(), so dispatcher tasks and
    # handlers execute on the same loop. This mirrors the known-working bot.
    acquire_instance_lock()
    try:
        app.run(bot_main())
    finally:
        release_instance_lock()


if __name__ == "__main__":
    main()
