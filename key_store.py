import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _connect(db_path: Path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def _now():
    return datetime.now(timezone.utc)


def init_db(db_path: Path):
    with _connect(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                key_prefix TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by INTEGER,
                expires_at TEXT,
                last_used_at TEXT,
                use_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        cols = {r[1] for r in con.execute("PRAGMA table_info(api_keys)").fetchall()}
        if "expires_at" not in cols:
            con.execute("ALTER TABLE api_keys ADD COLUMN expires_at TEXT")
        con.commit()


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_key(db_path: Path, name: str, created_by: int, days=None):
    raw = "htk_" + secrets.token_urlsafe(40)
    now = _now()
    expires = now + timedelta(days=days) if days else None
    with _connect(db_path) as con:
        cur = con.execute(
            "INSERT INTO api_keys(name,key_hash,key_prefix,enabled,created_at,created_by,expires_at) VALUES(?,?,?,?,?,?,?)",
            (name, hash_key(raw), raw[:12], 1, now.isoformat(), created_by, expires.isoformat() if expires else None),
        )
        con.commit()
    return {"id": cur.lastrowid, "name": name, "key": raw, "expires_at": expires.isoformat() if expires else None}


def list_keys(db_path: Path):
    with _connect(db_path) as con:
        return [dict(r) for r in con.execute(
            "SELECT id,name,key_prefix,enabled,created_at,created_by,expires_at,last_used_at,use_count FROM api_keys ORDER BY id DESC"
        ).fetchall()]


def get_key(db_path: Path, key_id: int):
    with _connect(db_path) as con:
        r = con.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        return dict(r) if r else None


def set_enabled(db_path: Path, key_id: int, enabled: bool):
    with _connect(db_path) as con:
        cur = con.execute("UPDATE api_keys SET enabled=? WHERE id=?", (1 if enabled else 0, key_id))
        con.commit()
        return cur.rowcount > 0


def rename_key(db_path: Path, key_id: int, name: str):
    if not name.strip():
        return False
    with _connect(db_path) as con:
        cur = con.execute("UPDATE api_keys SET name=? WHERE id=?", (name.strip(), key_id))
        con.commit()
        return cur.rowcount > 0


def set_expiry(db_path: Path, key_id: int, days=None):
    exp = (_now() + timedelta(days=days)).isoformat() if days else None
    with _connect(db_path) as con:
        cur = con.execute("UPDATE api_keys SET expires_at=? WHERE id=?", (exp, key_id))
        con.commit()
        return cur.rowcount > 0, exp


def delete_key(db_path: Path, key_id: int):
    with _connect(db_path) as con:
        cur = con.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        con.commit()
        return cur.rowcount > 0


def validate_key(db_path: Path, raw: str):
    if not raw:
        return None
    now = _now()
    with _connect(db_path) as con:
        r = con.execute("SELECT * FROM api_keys WHERE key_hash=? AND enabled=1", (hash_key(raw),)).fetchone()
        if not r:
            return None
        d = dict(r)
        if d.get("expires_at"):
            try:
                if datetime.fromisoformat(d["expires_at"]) <= now:
                    return None
            except Exception:
                return None
        con.execute("UPDATE api_keys SET last_used_at=?, use_count=use_count+1 WHERE id=?", (now.isoformat(), d["id"]))
        con.commit()
        return d
