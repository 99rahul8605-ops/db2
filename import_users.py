#!/usr/bin/env python3
import os
import re
import sqlite3
import time
import zipfile
from pathlib import Path

import ijson
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
ZIP_PATH = Path(os.getenv("ZIP_PATH", str(ROOT / "extracted/users_data.zip"))).resolve()
ZIP_MEMBER = os.getenv("ZIP_MEMBER", "users_data.json")
LOOKUP_DB = Path(os.getenv("LOOKUP_DB", str(ROOT / "lookup/users_lookup.db"))).resolve()
BATCH = int(os.getenv("IMPORT_BATCH_SIZE", "10000"))
COMMIT_EVERY = int(os.getenv("IMPORT_COMMIT_EVERY", "50000"))


def clean(v):
    if v is None: return None
    s = re.sub(r"\D+", "", str(v))
    return s or None


def txt(v):
    if v is None: return None
    return str(v).strip() or None


def oid(item):
    v = item.get("_id")
    return txt(v.get("$oid")) if isinstance(v, dict) else txt(v)


def init(c):
    c.execute("PRAGMA journal_mode=OFF")
    c.execute("PRAGMA synchronous=OFF")
    c.execute("PRAGMA temp_store=MEMORY")
    c.execute("PRAGMA cache_size=-262144")
    c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            mongo_oid TEXT, mobile TEXT, name TEXT, fname TEXT, address TEXT,
            alt TEXT, circle TEXT, ext_id TEXT, email TEXT
        )
    """)
    c.execute("CREATE TABLE IF NOT EXISTS import_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    c.commit()


def meta(c, k, d="0"):
    r = c.execute("SELECT value FROM import_meta WHERE key=?", (k,)).fetchone()
    return r[0] if r else d


def setmeta(c, k, v):
    c.execute(
        "INSERT INTO import_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (k, str(v)),
    )


def insert_batch(c, batch):
    c.executemany(
        "INSERT INTO users(mongo_oid,mobile,name,fname,address,alt,circle,ext_id,email) VALUES(?,?,?,?,?,?,?,?,?)",
        batch,
    )


def main():
    if not ZIP_PATH.exists():
        raise SystemExit(f"ZIP not found: {ZIP_PATH}")
    LOOKUP_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(LOOKUP_DB, timeout=60)
    init(c)
    processed = int(meta(c, "processed_records", "0"))
    inserted_total = int(meta(c, "inserted_records", "0"))
    if meta(c, "completed", "0") == "1" and meta(c, "phase", "") == "ready":
        print("Import + indexes already complete.", flush=True)
        return

    setmeta(c, "phase", "importing")
    setmeta(c, "completed", "0")
    setmeta(c, "updated_at", int(time.time()))
    c.commit()

    print(f"ZIP={ZIP_PATH}\nDB={LOOKUP_DB}\nResume checkpoint={processed:,}", flush=True)
    started = time.time()
    seen = 0
    inserted_run = 0
    batch = []

    with zipfile.ZipFile(ZIP_PATH) as z:
        info = z.getinfo(ZIP_MEMBER)
        setmeta(c, "source_json_bytes", info.file_size)
        c.commit()
        print(f"JSON uncompressed={info.file_size / 1024**3:.2f} GiB", flush=True)
        with z.open(ZIP_MEMBER) as f:
            for item in ijson.items(f, "item"):
                seen += 1
                if seen <= processed:
                    if seen % 1_000_000 == 0:
                        print(f"Resume scan skipped {seen:,}", flush=True)
                    continue
                if not isinstance(item, dict):
                    continue
                mobile, alt = clean(item.get("mobile")), clean(item.get("alt"))
                if not mobile and not alt:
                    continue
                batch.append((
                    oid(item), mobile, txt(item.get("name")), txt(item.get("fname")),
                    txt(item.get("address")), alt, txt(item.get("circle")),
                    txt(item.get("id")), txt(item.get("email")),
                ))
                if len(batch) >= BATCH:
                    insert_batch(c, batch)
                    inserted_run += len(batch)
                    batch.clear()
                if seen % COMMIT_EVERY == 0:
                    if batch:
                        insert_batch(c, batch)
                        inserted_run += len(batch)
                        batch.clear()
                    elapsed = max(0.1, time.time() - started)
                    rate = int((seen - processed) / elapsed)
                    setmeta(c, "processed_records", seen)
                    setmeta(c, "inserted_records", inserted_total + inserted_run)
                    setmeta(c, "last_rate", rate)
                    setmeta(c, "phase", "importing")
                    setmeta(c, "updated_at", int(time.time()))
                    c.commit()
                    print(f"processed={seen:,} inserted_total={inserted_total+inserted_run:,} rate={rate:,}/s", flush=True)

    if batch:
        insert_batch(c, batch)
        inserted_run += len(batch)
    setmeta(c, "processed_records", seen)
    setmeta(c, "inserted_records", inserted_total + inserted_run)
    setmeta(c, "phase", "indexing")
    setmeta(c, "updated_at", int(time.time()))
    c.commit()

    print("Building indexes...", flush=True)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_mobile ON users(mobile)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_alt ON users(alt)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_extid ON users(ext_id)")
    c.execute("ANALYZE")
    setmeta(c, "phase", "ready")
    setmeta(c, "completed", "1")
    setmeta(c, "updated_at", int(time.time()))
    c.commit()
    print(f"Done. inserted_total={inserted_total+inserted_run:,}", flush=True)
    c.close()


if __name__ == "__main__":
    main()
