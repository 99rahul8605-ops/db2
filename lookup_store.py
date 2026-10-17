import re
import sqlite3
from pathlib import Path


def normalize_number(value: str) -> str:
    n = re.sub(r"\D+", "", value or "")
    if not (7 <= len(n) <= 15):
        raise ValueError("Number must contain 7-15 digits")
    return n


def _connect_ro(path: Path):
    if not path.exists():
        raise FileNotFoundError(str(path))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def import_meta(path: Path):
    if not path.exists():
        return {"exists": False, "completed": False, "processed_records": 0, "indexed_rows": 0, "mode": None}
    with _connect_ro(path) as conn:
        table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_meta'").fetchone()
        if not table:
            return {"exists": True, "completed": False, "processed_records": 0, "indexed_rows": 0, "mode": None}
        rows = conn.execute("SELECT key,value FROM import_meta").fetchall()
        meta = {r["key"]: r["value"] for r in rows}
    return {
        "exists": True,
        "completed": meta.get("completed") == "1",
        "processed_records": int(meta.get("processed_records", "0") or 0),
        "indexed_rows": int(meta.get("indexed_rows", "0") or 0),
        "mode": meta.get("mode"),
        "schema_version": meta.get("schema_version"),
    }


def _columns(full: bool):
    if full:
        return "row_id,mongo_oid AS _id,mobile,name,fname,address,alt,circle,ext_id AS id,email"
    return "row_id,mobile,alt,name,fname,circle,ext_id AS id"


def lookup_number(path: Path, number: str, limit: int = 10, full: bool = True):
    number = normalize_number(number)
    limit = max(1, min(limit, 50))
    found, seen = [], set()
    cols = _columns(full)
    with _connect_ro(path) as conn:
        for r in conn.execute(f"SELECT {cols} FROM users WHERE mobile=? LIMIT ?", (number, limit)).fetchall():
            d = dict(r); rid = d.pop("row_id"); seen.add(rid); found.append(d)
        if len(found) < limit:
            for r in conn.execute(f"SELECT {cols} FROM users WHERE alt=? LIMIT ?", (number, limit)).fetchall():
                d = dict(r); rid = d.pop("row_id")
                if rid in seen:
                    continue
                seen.add(rid); found.append(d)
                if len(found) >= limit:
                    break
    return found


def lookup_id(path: Path, record_id: str, limit: int = 10, full: bool = True):
    rid = re.sub(r"\s+", "", record_id or "")
    if not rid or len(rid) > 64:
        raise ValueError("Invalid ID")
    cols = _columns(full)
    with _connect_ro(path) as conn:
        out = []
        for r in conn.execute(f"SELECT {cols} FROM users WHERE ext_id=? LIMIT ?", (rid, max(1, min(limit, 50)))).fetchall():
            d = dict(r); d.pop("row_id", None); out.append(d)
        return out
