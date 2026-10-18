#!/usr/bin/env python3
"""Resume only the missing SQLite indexes in an existing Hitek lookup DB.

This intentionally NEVER reads users_data.zip or re-imports records.

Install this file in your Git repository root alongside bot.py. Run:
    python3 recover_indexes_lowram.py --check
    python3 -u recover_indexes_lowram.py --run

Large indexes take hours on low-memory hosts. Each index is committed on its
own: if the process is interrupted, rerunning --run skips committed indexes.
"""

import argparse
import fcntl
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "lookup" / "users_lookup.db"
EXPECTED = (
    ("idx_users_mobile", "mobile"),
    ("idx_users_alt", "alt"),
    ("idx_users_extid", "ext_id"),
)
REQUIRED_COLUMNS = {"row_id", "mobile", "alt", "ext_id"}


def stamp(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def database_path(override):
    if override:
        return Path(override).expanduser().resolve()
    # Read only LOOKUP_DB from .env; do not evaluate shell or display secrets.
    dotenv = ROOT / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("LOOKUP_DB="):
                name = line.partition("=")[2].strip().strip('"').strip("'")
                if name:
                    path = Path(name).expanduser()
                    return (path if path.is_absolute() else ROOT / path).resolve()
    return DEFAULT_DB


def connect(db, readonly):
    if not db.is_file():
        raise RuntimeError(f"Database not found: {db}")
    mode = "ro" if readonly else "rw"
    return sqlite3.connect(db.as_uri() + f"?mode={mode}", uri=True, timeout=30, isolation_level=None)


def get_meta(conn):
    rows = conn.execute("SELECT key, value FROM import_meta").fetchall()
    return {str(k): str(v) for k, v in rows}


def validate_schema(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    missing = REQUIRED_COLUMNS - existing
    if missing:
        raise RuntimeError(f"Unexpected schema; users table missing: {sorted(missing)}")
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "import_meta" not in names:
        raise RuntimeError("import_meta table is missing; refusing recovery")


def installed_indexes(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='users'")}


def verify_indexes(conn):
    """Never mistake an unrelated index with the same name for a valid one."""
    installed = installed_indexes(conn)
    for name, column in EXPECTED:
        if name not in installed:
            continue
        actual = [row[2] for row in conn.execute(f'PRAGMA index_info("{name}")')]
        if actual != [column]:
            raise RuntimeError(f"Index {name} exists on {actual}, not on {column}. Refusing to overwrite.")
    return installed


def check(db):
    with connect(db, readonly=True) as conn:
        validate_schema(conn)
        meta = get_meta(conn)
        present = verify_indexes(conn)
        print("Database:", db)
        print("Size: %.2f GiB" % (db.stat().st_size / 1024**3))
        print("Import phase:", meta.get("phase", "missing"))
        print("Completed:", meta.get("completed", "missing"))
        print("Processed checkpoint:", meta.get("processed_records", "missing"))
        print("Inserted checkpoint:", meta.get("inserted_records", "missing"))
        for name, _ in EXPECTED:
            print(name + ":", "READY" if name in present else "MISSING")
        return all(name in present for name, _ in EXPECTED)


def run(db, heartbeat_seconds):
    db.parent.mkdir(parents=True, exist_ok=True)
    # Lock file belongs to the lookup storage directory, NOT the Git worktree.
    lock_path = db.parent / ".index_recovery.lock"
    with open(lock_path, "a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another recovery process already holds the lock")

        # Keep SQLite sorting scratch on the same large-volume filesystem.
        tmp = db.parent / "index_tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        os.environ["SQLITE_TMPDIR"] = str(tmp)
        os.environ["TMPDIR"] = str(tmp)

        conn = connect(db, readonly=False)
        try:
            validate_schema(conn)
            meta = get_meta(conn)
            phase = meta.get("phase", "")
            if phase not in ("indexing", "ready"):
                raise RuntimeError(
                    f"Unexpected import phase={phase!r}; stop to avoid indexing during an unfinished import."
                )
            if int(meta.get("processed_records", "0")) <= 0:
                raise RuntimeError("No imported-record checkpoint; refusing to mark database ready")

            existing = verify_indexes(conn)
            if all(name in existing for name, _ in EXPECTED) and phase == "ready" and meta.get("completed") == "1":
                stamp("All indexes and ready marker already present; nothing to do.")
                return

            usage = shutil.disk_usage(db.parent)
            stamp(f"DB={db} | free disk={usage.free / 1024**3:.1f} GiB")
            stamp(f"Checkpoint: processed={meta.get('processed_records')} phase={phase}")
            stamp("Configuring SQLite for a memory-limited container...")

            conn.execute("PRAGMA temp_store=FILE")
            conn.execute("PRAGMA cache_size=-8192")  # ~8 MiB pager cache
            conn.execute("PRAGMA mmap_size=0")
            conn.execute("PRAGMA threads=1")
            conn.execute("PRAGMA soft_heap_limit=67108864")
            # CREATE INDEX should be crash-safe/transactional; avoid OFF.
            stamp("Switching to rollback-journal mode (may checkpoint an existing WAL)...")
            actual_mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower()
            if actual_mode != "delete":
                raise RuntimeError(f"Could not enable DELETE journal mode: got {actual_mode}")
            conn.execute("PRAGMA synchronous=NORMAL")

            heartbeat = {"last": time.monotonic(), "index": ""}

            def report_progress():
                now = time.monotonic()
                if now - heartbeat["last"] >= heartbeat_seconds:
                    stamp(f"Building {heartbeat['index']} ... still working")
                    heartbeat["last"] = now
                return 0

            conn.set_progress_handler(report_progress, 250_000)
            for name, column in EXPECTED:
                existing = verify_indexes(conn)
                if name in existing:
                    stamp(f"SKIP {name}: already committed")
                    continue
                heartbeat["index"] = name
                heartbeat["last"] = time.monotonic()
                start = time.monotonic()
                stamp(f"START {name} ON users({column})")
                # Autocommit mode makes each completed index durable independently.
                conn.execute(f'CREATE INDEX "{name}" ON users("{column}")')
                if name not in verify_indexes(conn):
                    raise RuntimeError(f"Index verification failed: {name}")
                stamp(f"DONE {name} ({time.monotonic()-start:.0f}s)")

            conn.set_progress_handler(None, 0)
            stamp("All expected indexes exist. Marking import_meta ready...")
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO import_meta(key,value) VALUES('phase','ready') "
                    "ON CONFLICT(key) DO UPDATE SET value='ready'"
                )
                conn.execute(
                    "INSERT INTO import_meta(key,value) VALUES('completed','1') "
                    "ON CONFLICT(key) DO UPDATE SET value='1'"
                )
                conn.execute(
                    "INSERT INTO import_meta(key,value) VALUES('updated_at',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(int(time.time())),),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            stamp("SUCCESS: indexes finished, database marked ready; use /api_start if necessary.")
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="Low-memory SQLite index-only recovery for Hitek lookup DB")
    parser.add_argument("--db", help="Override DB path; otherwise read LOOKUP_DB from .env")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check", action="store_true", help="Display progress and existing indexes (read only)")
    actions.add_argument("--run", action="store_true", help="Build only missing indexes; no JSON re-import")
    parser.add_argument("--heartbeat", type=int, default=60, help="Seconds between in-progress log lines")
    args = parser.parse_args()
    db = database_path(args.db)
    try:
        if args.check:
            check(db)
        else:
            if args.heartbeat < 5:
                raise RuntimeError("--heartbeat must be >=5 seconds")
            run(db, args.heartbeat)
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
        stamp(f"ERROR: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
