import asyncio
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from pyrogram.types import Message

ENV_FILE = Path(__file__).resolve().with_name(".env")
load_dotenv(ENV_FILE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("hitek-upload-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
}
BASE_DIR = Path(os.environ.get("BASE_DIR", "/opt/hitek-bot")).resolve()
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(BASE_DIR / "uploads"))).resolve()
EXTRACT_DIR = Path(os.environ.get("EXTRACT_DIR", str(BASE_DIR / "extracted"))).resolve()
STATE_DB = Path(os.environ.get("STATE_DB", str(BASE_DIR / "queue.db"))).resolve()
SESSION_DIR = Path(os.environ.get("SESSION_DIR", str(BASE_DIR / "session"))).resolve()

for p in (BASE_DIR, UPLOAD_DIR, EXTRACT_DIR, STATE_DB.parent, SESSION_DIR):
    p.mkdir(parents=True, exist_ok=True)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in .env")
if not API_ID or not API_HASH:
    raise RuntimeError("TELEGRAM_API_ID / TELEGRAM_API_HASH missing in .env")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS missing in .env")

app = Client(
    "hitek_upload_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir=str(SESSION_DIR),
)

queue: asyncio.Queue[int] = asyncio.Queue()
worker_task: Optional[asyncio.Task] = None
current_job_id: Optional[int] = None
current_name: Optional[str] = None
cancel_download = asyncio.Event()
operation_lock = asyncio.Lock()


def db_conn():
    con = sqlite3.connect(STATE_DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db_conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued',
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        # Jobs interrupted by restart should be queued again.
        con.execute(
            "UPDATE jobs SET status='queued', error=NULL, updated_at=CURRENT_TIMESTAMP "
            "WHERE status='downloading'"
        )


def safe_name(name: str) -> str:
    name = Path(name or "file.bin").name
    name = re.sub(r"[^A-Za-z0-9._()\- ]", "_", name)
    return name[:240] or "file.bin"


def human_size(n: int) -> str:
    x = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or u == "TB":
            return f"{x:.2f} {u}"
        x /= 1024
    return f"{x:.2f} TB"


def is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in ADMIN_IDS)


async def deny_if_needed(message: Message) -> bool:
    if not is_admin(message):
        await message.reply_text("⛔ Admin only.")
        return True
    return False


def create_job(chat_id: int, message_id: int, file_name: str, file_size: int) -> int:
    with db_conn() as con:
        cur = con.execute(
            "INSERT INTO jobs(chat_id,message_id,file_name,file_size,status) VALUES(?,?,?,?, 'queued')",
            (chat_id, message_id, file_name, file_size),
        )
        return int(cur.lastrowid)


def set_job(job_id: int, status: str, error: Optional[str] = None):
    with db_conn() as con:
        con.execute(
            "UPDATE jobs SET status=?, error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, error, job_id),
        )


def get_job(job_id: int):
    with db_conn() as con:
        return con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def queued_jobs():
    with db_conn() as con:
        return con.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY id"
        ).fetchall()


def overall_counts():
    with db_conn() as con:
        row = con.execute(
            "SELECT COUNT(*) total, "
            "SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) done, "
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed "
            "FROM jobs"
        ).fetchone()
        return int(row["total"] or 0), int(row["done"] or 0), int(row["failed"] or 0)


def status_counts():
    with db_conn() as con:
        rows = con.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}


def list_files():
    return sorted([p for p in UPLOAD_DIR.iterdir() if p.is_file()], key=lambda p: p.name)


def detect_7z_parts():
    pattern = re.compile(r"^(?P<base>.+\.7z)\.(?P<num>\d{3,})$", re.I)
    groups = {}
    for p in list_files():
        m = pattern.match(p.name)
        if not m:
            continue
        groups.setdefault(m.group("base"), []).append((int(m.group("num")), p))
    return groups


def validate_group(items):
    items = sorted(items)
    if not items:
        return False, "No parts found", []
    nums = [n for n, _ in items]
    expected = list(range(nums[0], nums[-1] + 1))
    missing = sorted(set(expected) - set(nums))
    if nums[0] != 1:
        missing = list(range(1, nums[0])) + missing
    if missing:
        preview = ", ".join(f".{n:03d}" for n in missing[:20])
        more = " ..." if len(missing) > 20 else ""
        return False, f"Missing {len(missing)} part(s): {preview}{more}", missing
    return True, f"{len(items)} contiguous parts (.001-.{nums[-1]:03d})", []


async def send_long(chat_id: int, text: str):
    text = text or "(no output)"
    # Telegram message safety margin.
    chunks = [text[i:i+3800] for i in range(0, len(text), 3800)]
    for chunk in chunks[:6]:
        await app.send_message(chat_id, f"```\n{chunk}\n```")
    if len(chunks) > 6:
        await app.send_message(chat_id, "⚠️ Output truncated in Telegram.")


async def run_process(args: list[str], cwd: Optional[Path] = None, timeout: Optional[int] = None):
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        out, _ = await proc.communicate()
        raise RuntimeError(f"Command timed out.\n{out.decode(errors='replace')[-5000:]}")
    text = out.decode(errors="replace")
    return proc.returncode, text


async def run_process_live_7z(args: list[str], status_msg: Message, extract_dir: Path):
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    buf = ""
    tail_parts = []
    last_edit = 0.0
    pct_re = re.compile(r"(?:^|\s)(\d{1,3})%")
    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            break
        text = chunk.decode(errors="replace")
        tail_parts.append(text)
        if sum(map(len, tail_parts)) > 30000:
            joined = "".join(tail_parts)[-20000:]
            tail_parts = [joined]
        buf = (buf + text)[-8000:]
        matches = pct_re.findall(buf.replace("\r", "\n"))
        pct = int(matches[-1]) if matches else None
        now = asyncio.get_running_loop().time()
        if now - last_edit >= 4:
            disk = shutil.disk_usage(extract_dir)
            try:
                await status_msg.edit_text(
                    "📦 Extracting archive...\n"
                    + (f"Progress: {min(pct,100)}%\n" if pct is not None else "Progress: working...\n")
                    + f"Extracted folder: `{extract_dir}`\n"
                    + f"Free disk: {human_size(disk.free)}"
                )
            except Exception:
                pass
            last_edit = now
    code = await proc.wait()
    return code, "".join(tail_parts)[-20000:]


async def progress_cb(current, total, message: Message, name: str, state: dict):
    # Edit every ~3 seconds: smooth enough without hammering Telegram rate limits.
    loop = asyncio.get_running_loop()
    now = loop.time()
    if now - state.get("last_edit", 0) < 3 and current < total:
        return

    last_bytes = state.get("last_bytes", 0)
    last_time = state.get("last_time", state.get("started", now))
    dt = max(now - last_time, 0.001)
    inst_speed = max(current - last_bytes, 0) / dt
    started = state.get("started", now)
    avg_speed = current / max(now - started, 0.001)
    speed = inst_speed if inst_speed > 0 else avg_speed
    remaining = max((total or 0) - current, 0)
    eta = int(remaining / speed) if speed > 0 else 0
    pct = (current * 100 / total) if total else 0

    total_jobs, done_jobs, failed_jobs = overall_counts()
    active_number = min(done_jobs + failed_jobs + 1, total_jobs) if total_jobs else 1

    def fmt_eta(sec: int) -> str:
        if sec <= 0:
            return "--"
        h, rem = divmod(sec, 3600)
        m, ss = divmod(rem, 60)
        return f"{h}h {m}m" if h else (f"{m}m {ss}s" if m else f"{ss}s")

    text = (
        f"⬇️ Downloading `{name}`\n"
        f"{human_size(current)} / {human_size(total)}  •  {pct:.1f}%\n"
        f"Speed: {human_size(int(speed))}/s  •  ETA: {fmt_eta(eta)}\n"
        f"Overall: {active_number}/{total_jobs or '?'}  •  Done: {done_jobs}  •  Waiting: {queue.qsize()}"
    )
    try:
        await message.edit_text(text)
        state["last_edit"] = now
        state["last_bytes"] = current
        state["last_time"] = now
    except Exception:
        pass


async def download_worker():
    global current_job_id, current_name
    while True:
        job_id = await queue.get()
        current_job_id = job_id
        row = get_job(job_id)
        if not row:
            queue.task_done()
            continue

        chat_id = int(row["chat_id"])
        msg_id = int(row["message_id"])
        name = safe_name(row["file_name"])
        current_name = name
        cancel_download.clear()

        try:
            msg = await app.get_messages(chat_id, msg_id)
            if not msg or not msg.document:
                raise RuntimeError("Original Telegram document message not found.")

            destination = UPLOAD_DIR / name
            if destination.exists() and destination.stat().st_size == int(row["file_size"] or 0):
                set_job(job_id, "done")
                await app.send_message(chat_id, f"✅ Already exists: `{name}`")
                continue

            set_job(job_id, "downloading")
            status_msg = await app.send_message(
                chat_id,
                f"⬇️ Starting `{name}` ({human_size(row['file_size'])})\n"
                f"Queue remaining: {queue.qsize()}"
            )
            now = asyncio.get_running_loop().time()
            state = {"last_edit": 0.0, "last_bytes": 0, "last_time": now, "started": now}

            saved = await msg.download(
                file_name=str(destination),
                progress=progress_cb,
                progress_args=(status_msg, name, state),
            )
            if not saved:
                raise RuntimeError("Telegram download returned no path.")

            path = Path(saved)
            if path.resolve() != destination.resolve():
                await asyncio.to_thread(shutil.move, str(path), str(destination))

            set_job(job_id, "done")
            await status_msg.edit_text(
                f"✅ Saved `{name}`\n"
                f"Size: {human_size(destination.stat().st_size)}\n"
                f"Queue remaining: {queue.qsize()}"
            )
        except Exception as e:
            log.exception("Download failed for job %s", job_id)
            set_job(job_id, "failed", f"{type(e).__name__}: {e}")
            try:
                await app.send_message(
                    chat_id,
                    f"❌ Download failed: `{name}`\n{type(e).__name__}: {e}"
                )
            except Exception:
                pass
        finally:
            current_job_id = None
            current_name = None
            queue.task_done()




@app.on_message(filters.all, group=-100)
async def incoming_debug(client: Client, message: Message):
    """Log every incoming update before command/document handlers run."""
    try:
        log.info(
            "INCOMING chat=%s from=%s username=%s text=%r document=%s file=%s",
            message.chat.id if message.chat else None,
            message.from_user.id if message.from_user else None,
            message.from_user.username if message.from_user else None,
            message.text or message.caption,
            bool(message.document),
            message.document.file_name if message.document else None,
        )
    except Exception:
        log.exception("Failed to log incoming update")


@app.on_message(filters.command("whoami"), group=-10)
async def whoami_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    cid = message.chat.id if message.chat else None
    is_allowed = uid in ADMIN_IDS if uid is not None else False
    await message.reply_text(
        f"Your user ID: `{uid}`\n"
        f"Chat ID: `{cid}`\n"
        f"Admin allowed: `{'yes' if is_allowed else 'no'}`"
    )


@app.on_message(filters.command("ping"), group=-10)
async def ping_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    await message.reply_text("✅ Pong. Bot handlers are working.")

@app.on_message(filters.command(["start", "help"]))
async def help_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    await message.reply_text(
        "🗄 Hitek Upload/Extract Bot (Ubuntu)\n\n"
        "DIAGNOSTICS\n"
        "/ping - test bot response\n"
        "/whoami - show your Telegram user/chat ID\n\n"
        "UPLOAD\n"
        "• Forward/send documents → queued automatically\n"
        "• Only ONE file downloads at a time (low RAM)\n• Live % / speed / ETA / overall progress\n"
        "/queue - live queue/current download status\n"
        "/files - uploaded files summary\n"
        "/parts - check .7z.001/.002 sequence\n\n"
        "SERVER\n"
        "/disk - disk/RAM usage\n"
        "/du - upload/extract folder sizes\n\n"
        "ARCHIVE\n"
        "/test7z - run 7z test on detected .001\n"
        "/info7z - archive listing/info\n"
        "/extract7z CONFIRM - extract archive into EXTRACT_DIR\n\n"
        "MAINTENANCE\n"
        "/retryfailed - requeue failed downloads\n"
        "/clearqueue CONFIRM - remove waiting jobs (does not delete files)\n"
        "/deleteuploads CONFIRM - delete uploaded files\n"
        "/deleteextracted CONFIRM - delete extracted files\n\n"
        "⚠️ No unrestricted shell command is exposed through Telegram."
    )


@app.on_message(filters.document)
async def document_handler(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    doc = message.document
    name = safe_name(doc.file_name or f"{doc.file_unique_id}.bin")
    job_id = create_job(message.chat.id, message.id, name, int(doc.file_size or 0))
    await queue.put(job_id)
    await message.reply_text(
        f"📥 Queued #{job_id}: `{name}`\n"
        f"Size: {human_size(doc.file_size or 0)}\n"
        f"Queue position: {queue.qsize()}"
    )


@app.on_message(filters.command("queue"))
async def queue_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    counts = status_counts()
    lines = [
        f"Current: {current_name or '-'}",
        f"In-memory waiting: {queue.qsize()}",
        f"Queued: {counts.get('queued', 0)}",
        f"Downloading: {counts.get('downloading', 0)}",
        f"Done: {counts.get('done', 0)}",
        f"Failed: {counts.get('failed', 0)}",
    ]
    rows = queued_jobs()[:10]
    if rows:
        lines.append("\nNext:")
        lines.extend(f"#{r['id']} {r['file_name']} ({human_size(r['file_size'])})" for r in rows)
    await message.reply_text("\n".join(lines))


@app.on_message(filters.command("files"))
async def files_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    files = list_files()
    if not files:
        await message.reply_text("No uploaded files yet.")
        return
    total = sum(p.stat().st_size for p in files)
    lines = [f"Files: {len(files)} | Total: {human_size(total)}", ""]
    for p in files[:80]:
        lines.append(f"{p.name} — {human_size(p.stat().st_size)}")
    if len(files) > 80:
        lines.append(f"... +{len(files)-80} more")
    await message.reply_text("\n".join(lines))


@app.on_message(filters.command("parts"))
async def parts_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    groups = detect_7z_parts()
    if not groups:
        await message.reply_text("No *.7z.001 style parts found.")
        return
    lines = []
    for base, items in sorted(groups.items()):
        ok, msg, _ = validate_group(items)
        total = sum(p.stat().st_size for _, p in items)
        lines.append(f"{'✅' if ok else '⚠️'} {base}: {msg}; {human_size(total)}")
    await message.reply_text("\n".join(lines))


@app.on_message(filters.command("disk"))
async def disk_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    disk = shutil.disk_usage(BASE_DIR)
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            mem = f.read().splitlines()[:5]
    except Exception:
        mem = []
    await message.reply_text(
        f"Disk total: {human_size(disk.total)}\n"
        f"Disk used: {human_size(disk.used)}\n"
        f"Disk free: {human_size(disk.free)}\n\n" + "\n".join(mem)
    )


@app.on_message(filters.command("du"))
async def du_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    code, out = await run_process(["du", "-sh", str(UPLOAD_DIR), str(EXTRACT_DIR)])
    await send_long(message.chat.id, out)


def first_valid_group():
    groups = detect_7z_parts()
    if not groups:
        raise RuntimeError("No .7z.001 parts found.")
    # Prefer a complete group; otherwise use first for diagnostics.
    ordered = sorted(groups.items())
    for base, items in ordered:
        ok, _, _ = validate_group(items)
        if ok:
            return base, items
    return ordered[0]


@app.on_message(filters.command("test7z"))
async def test7z_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    if operation_lock.locked():
        await message.reply_text("⚠️ Another archive operation is already running.")
        return
    try:
        base, items = first_valid_group()
        ok, msg, _ = validate_group(items)
        if not ok:
            await message.reply_text(f"❌ Cannot test yet: {msg}")
            return
        first = sorted(items)[0][1]
        await message.reply_text(f"🧪 Testing `{first.name}`. This can take time.")
        async with operation_lock:
            code, out = await run_process(["7z", "t", str(first)])
        tail = out[-12000:]
        await send_long(message.chat.id, f"exit={code}\n{tail}")
    except Exception as e:
        await message.reply_text(f"❌ {type(e).__name__}: {e}")


@app.on_message(filters.command("info7z"))
async def info7z_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    try:
        base, items = first_valid_group()
        ok, msg, _ = validate_group(items)
        if not ok:
            await message.reply_text(f"❌ Archive incomplete: {msg}")
            return
        first = sorted(items)[0][1]
        code, out = await run_process(["7z", "l", "-slt", str(first)], timeout=1800)
        await send_long(message.chat.id, f"exit={code}\n{out[-18000:]}")
    except Exception as e:
        await message.reply_text(f"❌ {type(e).__name__}: {e}")


@app.on_message(filters.command("extract7z"))
async def extract7z_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    args = message.command or []
    if len(args) < 2 or args[1] != "CONFIRM":
        await message.reply_text("Use: /extract7z CONFIRM")
        return
    if operation_lock.locked():
        await message.reply_text("⚠️ Another archive operation is already running.")
        return
    try:
        base, items = first_valid_group()
        ok, msg, _ = validate_group(items)
        if not ok:
            await message.reply_text(f"❌ Archive incomplete: {msg}")
            return
        first = sorted(items)[0][1]
        disk = shutil.disk_usage(EXTRACT_DIR)
        status_msg = await message.reply_text(
            f"📦 Extracting `{first.name}`\n"
            f"Destination: `{EXTRACT_DIR}`\n"
            f"Free disk before start: {human_size(disk.free)}\n"
            "Progress: starting..."
        )
        EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
        async with operation_lock:
            code, out = await run_process_live_7z(
                ["7z", "x", str(first), f"-o{EXTRACT_DIR}", "-y", "-bb1", "-bsp1"],
                status_msg, EXTRACT_DIR
            )
        disk2 = shutil.disk_usage(EXTRACT_DIR)
        tail = out[-16000:]
        await send_long(
            message.chat.id,
            f"exit={code}\nFree disk now: {human_size(disk2.free)}\n\n{tail}",
        )
        if code == 0:
            try:
                await status_msg.edit_text(
                    f"✅ Extraction completed successfully.\nFree disk: {human_size(disk2.free)}"
                )
            except Exception:
                pass
        else:
            try:
                await status_msg.edit_text(
                    f"❌ Extraction failed / completed with exit code {code}.\n"
                    f"Free disk: {human_size(disk2.free)}"
                )
            except Exception:
                pass
    except Exception as e:
        log.exception("Extraction failed")
        await message.reply_text(f"❌ {type(e).__name__}: {e}")


@app.on_message(filters.command("retryfailed"))
async def retryfailed_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    with db_conn() as con:
        rows = con.execute("SELECT id FROM jobs WHERE status='failed' ORDER BY id").fetchall()
        con.execute("UPDATE jobs SET status='queued', error=NULL WHERE status='failed'")
    for r in rows:
        await queue.put(int(r["id"]))
    await message.reply_text(f"✅ Requeued {len(rows)} failed job(s).")


@app.on_message(filters.command("clearqueue"))
async def clearqueue_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    args = message.command or []
    if len(args) < 2 or args[1] != "CONFIRM":
        await message.reply_text("Use: /clearqueue CONFIRM")
        return
    removed = 0
    while True:
        try:
            job_id = queue.get_nowait()
            set_job(job_id, "cancelled", "Cleared by admin")
            queue.task_done()
            removed += 1
        except asyncio.QueueEmpty:
            break
    with db_conn() as con:
        con.execute("UPDATE jobs SET status='cancelled', error='Cleared by admin' WHERE status='queued'")
    await message.reply_text(f"✅ Cleared {removed} in-memory waiting job(s). Current download was not interrupted.")


@app.on_message(filters.command("deleteuploads"))
async def deleteuploads_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    args = message.command or []
    if len(args) < 2 or args[1] != "CONFIRM":
        await message.reply_text("Use: /deleteuploads CONFIRM")
        return
    if current_job_id is not None:
        await message.reply_text("❌ A download is active. Clear/finish downloads first.")
        return
    count = 0
    for p in list_files():
        p.unlink()
        count += 1
    await message.reply_text(f"✅ Deleted {count} uploaded file(s).")


@app.on_message(filters.command("deleteextracted"))
async def deleteextracted_cmd(client: Client, message: Message):
    if await deny_if_needed(message):
        return
    args = message.command or []
    if len(args) < 2 or args[1] != "CONFIRM":
        await message.reply_text("Use: /deleteextracted CONFIRM")
        return
    if operation_lock.locked():
        await message.reply_text("❌ Archive operation is active.")
        return
    if EXTRACT_DIR.exists():
        await asyncio.to_thread(shutil.rmtree, EXTRACT_DIR)
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    await message.reply_text("✅ Extracted folder cleared.")


async def bootstrap_queue():
    # Restore queued jobs after bot restart.
    for row in queued_jobs():
        await queue.put(int(row["id"]))


async def main():
    global worker_task
    init_db()
    await app.start()
    await bootstrap_queue()
    worker_task = asyncio.create_task(download_worker())
    me = await app.get_me()
    log.info("Started as @%s (id=%s)", me.username, me.id)
    log.info("Loaded .env from %s", ENV_FILE)
    log.info("Configured ADMIN_IDS=%s", sorted(ADMIN_IDS))

    # Best-effort startup notification. This succeeds only after an admin has
    # opened/started the bot at least once, which is useful as a health check.
    for admin_id in sorted(ADMIN_IDS):
        try:
            await app.send_message(
                admin_id,
                f"✅ @{me.username} started successfully.\n"
                "Send /ping to test handlers or /whoami to verify your ID.",
            )
        except Exception as exc:
            log.warning("Startup message to admin %s failed: %s", admin_id, exc)

    try:
        await idle()
    finally:
        if worker_task:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
