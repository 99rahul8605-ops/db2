import asyncio
import logging
import os
import fcntl
import re
import shutil
import sqlite3
import time
from contextlib import suppress
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import Message

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("hitek-upload-bot")
BUILD = "pyro-loopfix-2026-10-16-1"

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
PID_FILE = ROOT / ".bot.pid"
LOCK_FILE = ROOT / ".bot.lock"
_INSTANCE_LOCK_FH = None

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN missing in .env")
if not API_ID:
    raise SystemExit("TELEGRAM_API_ID missing/invalid in .env")
if not API_HASH:
    raise SystemExit("TELEGRAM_API_HASH missing in .env")
if not ADMIN_IDS:
    raise SystemExit("ADMIN_IDS missing in .env")

for p in (UPLOAD_DIR, EXTRACT_DIR, SESSION_DIR):
    p.mkdir(parents=True, exist_ok=True)
STATE_DB.parent.mkdir(parents=True, exist_ok=True)


def acquire_instance_lock():
    global _INSTANCE_LOCK_FH
    fh = open(LOCK_FILE, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise SystemExit(
            "Another hitek bot instance is already running. Stop it first with ./stop.sh"
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
)

# Safety: uploads are OFF after every process restart.
upload_mode = False
operation_lock = asyncio.Lock()
worker_task = None
current_job_id = None
current_name = None
current_started = None
cancel_current = False


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
        (p for p in UPLOAD_DIR.iterdir() if p.is_file() and not p.name.endswith(".downloading")),
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
        "🗄 Hitek Pyrogram Upload Bot\n\n"
        "Upload safety:\n"
        "/upload_on — enable accepting files\n"
        "/upload_off — disable accepting new files\n"
        "/upload_status — current mode\n\n"
        "Queue:\n"
        "/queue — queue summary\n"
        "/retryfailed — retry failed downloads\n"
        "/cancelcurrent CONFIRM — cancel current file and requeue it\n"
        "/files — saved files\n"
        "/parts — check .7z.001... sequence\n\n"
        "Archive:\n"
        "/test7z — verify archive\n"
        "/info7z — archive info\n"
        "/extract7z CONFIRM — extract archive\n\n"
        "System:\n"
        "/disk — disk + RAM\n"
        "/ping — bot test\n"
        "/whoami — show IDs\n"
        "/version — show running build\n\n"
        "Files can be forwarded in any order. Downloads run ONE file at a time."
    )


@app.on_message(filters.command("ping"))
@admin_only
async def ping_handler(client: Client, message: Message):
    await message.reply_text(f"✅ Pong — Pyrogram receiver is working.\nBuild: `{BUILD}`")


@app.on_message(filters.command("version"))
async def version_handler(client: Client, message: Message):
    await message.reply_text(f"Build: `{BUILD}`\nPID: `{os.getpid()}`")


@app.on_message(filters.command("whoami"))
async def whoami_handler(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    await message.reply_text(
        f"User ID: `{uid}`\nChat ID: `{message.chat.id}`\nAdmin: `{uid in ADMIN_IDS}`"
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
        f"Current: `{current_name or '-'}`\n"
        f"Queued: {c.get('queued', 0)}"
    )


@app.on_message(filters.command("upload_status"))
@admin_only
async def upload_status_handler(client: Client, message: Message):
    c = queue_counts()
    await message.reply_text(
        f"Upload mode: {'🟢 ON' if upload_mode else '🔴 OFF'}\n"
        f"Current: `{current_name or '-'}`\n"
        f"Queued: {c.get('queued', 0)}\n"
        f"Done: {c.get('done', 0)} • Failed: {c.get('failed', 0)}"
    )


@app.on_message(filters.command("queue"))
@admin_only
async def queue_handler(client: Client, message: Message):
    c = queue_counts()
    text = (
        f"Current: `{current_name or '-'}`\n"
        f"Queued: {c.get('queued', 0)}\n"
        f"Downloading: {c.get('downloading', 0)}\n"
        f"Done: {c.get('done', 0)}\n"
        f"Failed: {c.get('failed', 0)}"
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


@app.on_message(filters.command("test7z"))
@admin_only
async def test7z_handler(client: Client, message: Message):
    if operation_lock.locked():
        return await message.reply_text("⚠️ Archive operation already running.")
    base, items = choose_group(True)
    first = items[0][1]
    status = await message.reply_text(f"🧪 Testing `{first.name}` ...")
    async with operation_lock:
        code, out = await run_cmd(["7z", "t", str(first)])
    tail = out[-3500:]
    await edit_safe(status, f"{'✅' if code == 0 else '❌'} 7z test exit={code}\n```\n{tail}\n```")


@app.on_message(filters.command("info7z"))
@admin_only
async def info7z_handler(client: Client, message: Message):
    base, items = choose_group(True)
    first = items[0][1]
    code, out = await run_cmd(["7z", "l", "-slt", str(first)])
    await message.reply_text(f"exit={code}\n```\n{out[-3500:]}\n```")


@app.on_message(filters.command("extract7z"))
@admin_only
async def extract7z_handler(client: Client, message: Message):
    if len(message.command or []) < 2 or message.command[1] != "CONFIRM":
        return await message.reply_text("Use: `/extract7z CONFIRM`")
    if operation_lock.locked():
        return await message.reply_text("⚠️ Archive operation already running.")
    _, items = choose_group(True)
    first = items[0][1]
    status = await message.reply_text(f"📦 Extracting `{first.name}` ...")
    async with operation_lock:
        proc = await asyncio.create_subprocess_exec(
            "7z", "x", str(first), f"-o{EXTRACT_DIR}", "-y", "-bb1", "-bsp1",
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
            if now - last_edit >= 4:
                matches = pct_re.findall(tail.replace("\r", "\n"))
                pct = matches[-1] if matches else "working"
                free = shutil.disk_usage(EXTRACT_DIR).free
                await edit_safe(
                    status,
                    f"📦 Extracting `{first.name}`\n"
                    f"Progress: {pct}{'%' if str(pct).isdigit() else ''}\n"
                    f"Free disk: {human_size(free)}",
                )
                last_edit = now
        code = await proc.wait()
    await edit_safe(
        status,
        f"{'✅' if code == 0 else '❌'} Extraction finished. Exit={code}\n"
        f"Free disk: {human_size(shutil.disk_usage(EXTRACT_DIR).free)}",
    )
    if code != 0:
        await message.reply_text(f"```\n{tail[-3500:]}\n```")


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
            f"⛔ Upload mode OFF. `{name}` was NOT queued.\nUse /upload_on first."
        )

    job_id, inserted = add_job(message.chat.id, message.id, name, size, doc.file_id)
    if not inserted:
        with db_conn() as con:
            row = con.execute("SELECT status FROM queue WHERE id=?", (job_id,)).fetchone()
        return await message.reply_text(f"ℹ️ Already known: `{name}` • status={row['status']}")

    position = waiting_ahead(job_id) + (1 if current_job_id else 0) + 1
    await message.reply_text(
        f"📥 Queued `{name}`\n"
        f"Size: {human_size(size)}\n"
        f"Queue position: {position}"
    )


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
            msg = None
            expected = int(job["file_size"] or 0)
            media_source = job.get("file_id")
            if not media_source:
                # Backward-compatible fallback for jobs queued by older versions.
                msg = await app.get_messages(job["chat_id"], job["message_id"])
                if not msg or not msg.document:
                    raise RuntimeError("Original Telegram document message is unavailable")
                media_source = msg
                expected = int(expected or msg.document.file_size or 0)

            # If already complete, don't download it again.
            if final.exists() and (not expected or final.stat().st_size == expected):
                set_job_status(job["id"], "done")
                with suppress(Exception):
                    await app.send_message(
                        job["chat_id"],
                        f"✅ Already exists, skipped: `{final.name}` ({human_size(final.stat().st_size)})",
                    )
                continue

            with suppress(FileNotFoundError):
                temp.unlink()

            counts = queue_counts()
            status = await app.send_message(
                job["chat_id"],
                f"⬇️ Starting `{job['file_name']}`\n"
                f"Size: {human_size(expected)}\n"
                f"Waiting: {counts.get('queued', 0)}",
            )

            state = {
                "last_edit": 0.0,
                "last_bytes": 0,
                "last_t": current_started,
                "smoothed": 0.0,
            }

            async def progress(current, total):
                if cancel_current:
                    # Pyrogram's stop_transmission cancels this active download.
                    client = app
                    client.stop_transmission()
                    return

                now = time.monotonic()
                if current < total and now - state["last_edit"] < 3.0:
                    return

                dt = max(now - state["last_t"], 0.001)
                inst = max(0, current - state["last_bytes"]) / dt
                if state["smoothed"] <= 0:
                    state["smoothed"] = inst
                elif inst > 0:
                    state["smoothed"] = state["smoothed"] * 0.70 + inst * 0.30
                speed = state["smoothed"] or (current / max(now - current_started, 0.001))
                eta = ((total - current) / speed) if total and speed > 0 else None
                pct = (current * 100 / total) if total else 0
                counts2 = queue_counts()
                text = (
                    f"⬇️ Downloading `{job['file_name']}`\n"
                    f"[{progress_bar(current, total)}] {pct:.1f}%\n"
                    f"{human_size(current)} / {human_size(total)}\n"
                    f"Speed: {human_size(speed)}/s • ETA: {fmt_eta(eta)}\n"
                    f"Waiting: {counts2.get('queued', 0)}"
                )
                await edit_safe(status, text)
                state["last_edit"] = now
                state["last_bytes"] = current
                state["last_t"] = now

            saved = await app.download_media(
                media_source,
                file_name=str(temp),
                progress=progress,
            )

            if cancel_current:
                raise asyncio.CancelledError("cancel requested")
            if not saved or not temp.exists():
                raise RuntimeError("Download stopped before completion")
            if expected and temp.stat().st_size != expected:
                raise RuntimeError(
                    f"Size mismatch: got {temp.stat().st_size}, expected {expected}"
                )

            os.replace(temp, final)
            elapsed = max(time.monotonic() - current_started, 0.001)
            avg = final.stat().st_size / elapsed
            set_job_status(job["id"], "done")
            counts3 = queue_counts()
            await edit_safe(
                status,
                f"✅ Downloaded `{final.name}`\n"
                f"Size: {human_size(final.stat().st_size)}\n"
                f"Average: {human_size(avg)}/s\n"
                f"Waiting: {counts3.get('queued', 0)} • Done: {counts3.get('done', 0)}",
            )

        except asyncio.CancelledError:
            set_job_status(job["id"], "queued", "cancelled and requeued")
            with suppress(FileNotFoundError):
                temp.unlink()
            if status:
                await edit_safe(status, f"⏸ Cancelled `{job['file_name']}` and requeued it.")
        except Exception as e:
            log.exception("Download failed for job %s", job["id"])
            set_job_status(job["id"], "failed", f"{type(e).__name__}: {e}")
            with suppress(FileNotFoundError):
                temp.unlink()
            text = f"❌ Download failed `{job['file_name']}`\n{type(e).__name__}: {e}"
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
    global worker_task
    init_db()
    await app.start()
    me = await app.get_me()
    log.info("Started as @%s (id=%s) build=%s", me.username, me.id, BUILD)
    log.info("Pyrogram/PyroFork version active; .env=%s", ROOT / ".env")
    log.info("ADMIN_IDS=%s", sorted(ADMIN_IDS))
    log.info("Upload mode starts OFF")

    worker_task = asyncio.create_task(download_worker(), name="download-worker")
    for uid in ADMIN_IDS:
        with suppress(Exception):
            await app.send_message(
                uid,
                f"✅ @{me.username} started with Pyrogram. Upload mode is OFF.\nBuild: `{BUILD}`\nSend /ping.",
            )
    try:
        await idle()
    finally:
        if worker_task:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
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
