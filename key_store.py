import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(path: Path):
    with _connect(path) as conn:
        conn.execute("""
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
        conn.commit()


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_key(path: Path, name: str, created_by: int | None = None, days: int | None = None):
    init_db(path)
    name = name.strip()
    if not name:
        raise ValueError("Key name required")
    raw = "hk_" + secrets.token_urlsafe(40)
    now = _now()
    expires = now + timedelta(days=days) if days and days > 0 else None
    with _connect(path) as conn:
        cur = conn.execute("""
            INSERT INTO api_keys(name,key_hash,key_prefix,enabled,created_at,created_by,expires_at)
            VALUES(?,?,?,?,?,?,?)
        """, (name, hash_key(raw), raw[:12], 1, _iso(now), created_by, _iso(expires)))
        conn.commit()
        key_id = cur.lastrowid
    return {"id": key_id, "name": name, "key": raw, "prefix": raw[:12], "created_at": _iso(now), "expires_at": _iso(expires)}


def list_keys(path: Path, limit: int = 100):
    init_db(path)
    with _connect(path) as conn:
        rows = conn.execute("""
            SELECT id,name,key_prefix,enabled,created_at,created_by,expires_at,last_used_at,use_count
            FROM api_keys ORDER BY id DESC LIMIT ?
        """, (max(1, min(limit, 500)),)).fetchall()
        return [dict(r) for r in rows]


def get_key(path: Path, key_id: int):
    init_db(path)
    with _connect(path) as conn:
        row = conn.execute("""
            SELECT id,name,key_prefix,enabled,created_at,created_by,expires_at,last_used_at,use_count
            FROM api_keys WHERE id=?
        """, (key_id,)).fetchone()
        return dict(row) if row else None


def set_enabled(path: Path, key_id: int, enabled: bool) -> bool:
    with _connect(path) as conn:
        cur = conn.execute("UPDATE api_keys SET enabled=? WHERE id=?", (1 if enabled else 0, key_id))
        conn.commit()
        return cur.rowcount > 0


def rename_key(path: Path, key_id: int, name: str) -> bool:
    name = name.strip()
    if not name:
        return False
    with _connect(path) as conn:
        cur = conn.execute("UPDATE api_keys SET name=? WHERE id=?", (name, key_id))
        conn.commit()
        return cur.rowcount > 0


def set_expiry(path: Path, key_id: int, days: int | None) -> bool:
    expires = None if not days or days <= 0 else _now() + timedelta(days=days)
    with _connect(path) as conn:
        cur = conn.execute("UPDATE api_keys SET expires_at=? WHERE id=?", (_iso(expires), key_id))
        conn.commit()
        return cur.rowcount > 0


def delete_key(path: Path, key_id: int) -> bool:
    with _connect(path) as conn:
        cur = conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        conn.commit()
        return cur.rowcount > 0


def validate_key(path: Path, raw: str, record_use: bool = True):
    if not raw:
        return None
    init_db(path)
    h = hash_key(raw)
    now = _now()
    with _connect(path) as conn:
        row = conn.execute("""
            SELECT id,name,key_prefix,enabled,created_at,created_by,expires_at,last_used_at,use_count
            FROM api_keys WHERE key_hash=?
        """, (h,)).fetchone()
        if not row or not row["enabled"]:
            return None
        if row["expires_at"]:
            try:
                exp = datetime.fromisoformat(row["expires_at"])
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp <= now:
                    return None
            except ValueError:
                return None
        if record_use:
            conn.execute("UPDATE api_keys SET last_used_at=?, use_count=use_count+1 WHERE id=?", (_iso(now), row["id"]))
            conn.commit()
        return dict(row)


def stats(path: Path):
    init_db(path)
    now = _iso(_now())
    with _connect(path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0]
        active = conn.execute("""
            SELECT COUNT(*) FROM api_keys
            WHERE enabled=1 AND (expires_at IS NULL OR expires_at>?)
        """, (now,)).fetchone()[0]
        uses = conn.execute("SELECT COALESCE(SUM(use_count),0) FROM api_keys").fetchone()[0]
    return {"total": total, "active": active, "uses": uses}
