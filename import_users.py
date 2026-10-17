#!/usr/bin/env python3
import argparse, json, re, sqlite3, time, zipfile
from pathlib import Path
import ijson
from config import load_settings

SCHEMA_VERSION = "2-full-fields"

def clean_number(value):
    if value is None: return None
    s = re.sub(r"\D+", "", str(value)); return s or None

def txt(value):
    if value is None: return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    s = str(value).strip(); return s or None

def oid_value(item):
    v = item.get("_id")
    return txt(v.get("$oid")) if isinstance(v, dict) else txt(v)

def clean_db_files(path: Path):
    for p in (path, Path(str(path)+"-wal"), Path(str(path)+"-shm")):
        try: p.unlink()
        except FileNotFoundError: pass

def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60); conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY"); conn.execute("PRAGMA cache_size=-524288")
    conn.execute("PRAGMA mmap_size=268435456")
    return conn

def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            mongo_oid TEXT, mobile TEXT, name TEXT, fname TEXT, address TEXT,
            alt TEXT, circle TEXT, ext_id TEXT, email TEXT
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS import_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()

def get_meta(conn, key, default=None):
    r = conn.execute("SELECT value FROM import_meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default

def set_meta(conn, key, value):
    conn.execute("INSERT INTO import_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

def insert_batch(conn, batch):
    if not batch: return 0
    conn.executemany("INSERT INTO users(mongo_oid,mobile,name,fname,address,alt,circle,ext_id,email) VALUES(?,?,?,?,?,?,?,?,?)", batch)
    n = len(batch); batch.clear(); return n

def build_indexes(conn):
    print("\nBuilding indexes: mobile, alt, id...", flush=True); t=time.time()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_mobile ON users(mobile)"); print("mobile index done", flush=True)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_alt ON users(alt)"); print("alt index done", flush=True)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_ext_id ON users(ext_id)"); print("id index done", flush=True)
    conn.execute("ANALYZE"); conn.commit(); print(f"Indexes ready in {(time.time()-t)/60:.1f} min", flush=True)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--fresh", action="store_true"); ap.add_argument("--limit", type=int, default=0); ap.add_argument("--skip-index", action="store_true"); ap.add_argument("--status", action="store_true"); args=ap.parse_args()
    cfg=load_settings()
    if args.fresh: clean_db_files(cfg.db_path)
    if args.status:
        if not cfg.db_path.exists(): print("DB does not exist yet"); return
        conn=connect(cfg.db_path); init_db(conn)
        for r in conn.execute("SELECT key,value FROM import_meta ORDER BY key").fetchall(): print(f"{r['key']}={r['value']}")
        print("db_size_bytes=", cfg.db_path.stat().st_size); return
    if not cfg.zip_path.exists(): raise SystemExit(f"ZIP not found: {cfg.zip_path}")
    conn=connect(cfg.db_path); init_db(conn)
    schema=get_meta(conn,"schema_version")
    if schema and schema != SCHEMA_VERSION: raise SystemExit(f"Existing schema={schema}; use --fresh")
    set_meta(conn,"schema_version",SCHEMA_VERSION); conn.commit()
    if get_meta(conn,"completed","0") == "1" and not args.fresh: print("Import already complete; use --fresh to rebuild."); return
    processed=int(get_meta(conn,"processed_records","0") or 0)
    print("Source ZIP:",cfg.zip_path,"\nJSON:",cfg.zip_member,"\nDB:",cfg.db_path,"\nResume:",f"{processed:,}",flush=True)
    started=time.time(); seen=0; inserted_run=0; batch=[]
    with zipfile.ZipFile(cfg.zip_path) as zf:
        info=zf.getinfo(cfg.zip_member); print(f"JSON uncompressed: {info.file_size/1024**3:.2f} GiB",flush=True)
        with zf.open(cfg.zip_member,"r") as fp:
            for item in ijson.items(fp,"item"):
                seen += 1
                if args.limit and seen > args.limit: break
                if seen <= processed:
                    if seen % 1_000_000 == 0: print(f"Resume skipped {seen:,}",flush=True)
                    continue
                if not isinstance(item,dict): continue
                mobile,alt=clean_number(item.get("mobile")),clean_number(item.get("alt"))
                if not mobile and not alt: continue
                batch.append((oid_value(item),mobile,txt(item.get("name")),txt(item.get("fname")),txt(item.get("address")),alt,txt(item.get("circle")),txt(item.get("id")),txt(item.get("email"))))
                if len(batch)>=cfg.import_batch_size: inserted_run += insert_batch(conn,batch)
                if seen % cfg.import_commit_every == 0:
                    inserted_run += insert_batch(conn,batch); total=conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                    set_meta(conn,"processed_records",seen); set_meta(conn,"indexed_rows",total); set_meta(conn,"mode","sample" if args.limit else "full"); conn.commit()
                    rate=max(.1,(seen-processed)/max(.1,time.time()-started)); print(f"processed={seen:,} | rows={total:,} | {rate:,.0f} rec/s | db={cfg.db_path.stat().st_size/1024**3:.2f} GiB",flush=True)
    inserted_run += insert_batch(conn,batch); total=conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    set_meta(conn,"processed_records",seen); set_meta(conn,"indexed_rows",total); set_meta(conn,"completed","1"); set_meta(conn,"mode","sample" if args.limit else "full"); conn.commit()
    print(f"Import complete. source={seen:,} rows={total:,}",flush=True)
    if not args.skip_index: build_indexes(conn)
    conn.close(); print("READY:",cfg.db_path,flush=True)

if __name__=="__main__": main()
