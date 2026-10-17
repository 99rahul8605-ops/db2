import os
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query

import key_store

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
LOOKUP_DB = Path(os.getenv("LOOKUP_DB", str(ROOT / "lookup/users_lookup.db"))).resolve()
KEYS_DB = Path(os.getenv("KEYS_DB", str(ROOT / "api_keys.db"))).resolve()
MAX_RESULTS = max(1, min(50, int(os.getenv("MAX_LOOKUP_RESULTS", "10") or 10)))
RATE_LIMIT_PER_MIN = max(1, min(10000, int(os.getenv("RATE_LIMIT_PER_MIN", "60") or 60)))
_RATE_LOCK = threading.Lock()
_RATE = defaultdict(deque)

key_store.init_db(KEYS_DB)
app = FastAPI(title="Hitek Private Lookup API", docs_url=None, redoc_url=None)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _check_rate_limit(key_id: int):
    now = time.monotonic()
    cutoff = now - 60.0
    with _RATE_LOCK:
        q = _RATE[key_id]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= RATE_LIMIT_PER_MIN:
            raise HTTPException(429, "Rate limit exceeded")
        q.append(now)


def auth(x_api_key):
    info = key_store.validate_key(KEYS_DB, x_api_key or "")
    if not info:
        raise HTTPException(401, "Invalid, disabled, or expired API key")
    _check_rate_limit(int(info["id"]))
    return info


def con():
    if not LOOKUP_DB.exists():
        raise HTTPException(503, "Lookup DB not ready")
    c = sqlite3.connect(f"file:{LOOKUP_DB}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    return c


def num(v):
    n = re.sub(r"\D+", "", v or "")
    if not (7 <= len(n) <= 15):
        raise HTTPException(400, "Invalid number")
    return n


def rows_out(rows):
    return [
        {
            "_id": r["mongo_oid"], "mobile": r["mobile"], "name": r["name"],
            "fname": r["fname"], "address": r["address"], "alt": r["alt"],
            "circle": r["circle"], "id": r["ext_id"], "email": r["email"],
        }
        for r in rows
    ]


@app.get("/health")
def health():
    ready = False
    phase = None
    if LOOKUP_DB.exists():
        try:
            with sqlite3.connect(f"file:{LOOKUP_DB}?mode=ro", uri=True, timeout=5) as c:
                meta = dict(c.execute("SELECT key,value FROM import_meta").fetchall())
                phase = meta.get("phase")
                ready = meta.get("completed") == "1" and phase in (None, "ready")
        except Exception:
            pass
    return {"ok": True, "db_ready": ready, "phase": phase}


@app.get("/lookup")
def lookup(number: str = Query(...), x_api_key: str | None = Header(None, alias="X-API-Key")):
    auth(x_api_key)
    n = num(number)
    with con() as c:
        rows = c.execute(
            "SELECT mongo_oid,mobile,name,fname,address,alt,circle,ext_id,email FROM users WHERE mobile=? OR alt=? LIMIT ?",
            (n, n, MAX_RESULTS),
        ).fetchall()
    return {"found": bool(rows), "number": n, "count": len(rows), "results": rows_out(rows)}


@app.get("/id/{record_id}")
def by_id(record_id: str, x_api_key: str | None = Header(None, alias="X-API-Key")):
    auth(x_api_key)
    with con() as c:
        rows = c.execute(
            "SELECT mongo_oid,mobile,name,fname,address,alt,circle,ext_id,email FROM users WHERE ext_id=? LIMIT ?",
            (record_id, MAX_RESULTS),
        ).fetchall()
    return {"found": bool(rows), "count": len(rows), "results": rows_out(rows)}


@app.get("/stats")
def stats(x_api_key: str | None = Header(None, alias="X-API-Key")):
    auth(x_api_key)
    with con() as c:
        count = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return {"rows": count, "db_bytes": LOOKUP_DB.stat().st_size}
