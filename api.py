#!/usr/bin/env python3
import threading, time
from collections import defaultdict, deque
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from config import load_settings
import key_store, lookup_store

cfg=load_settings(); key_store.init_db(cfg.keys_db_path)
app=FastAPI(title="Private Users Lookup API",version="2.0",docs_url=None,redoc_url=None,openapi_url=None)
_rate=defaultdict(deque); _lock=threading.Lock()

def rate_limit(key_id:int):
    now=time.monotonic(); cutoff=now-60
    with _lock:
        q=_rate[key_id]
        while q and q[0]<cutoff: q.popleft()
        if len(q)>=cfg.api_rate_limit_per_minute: raise HTTPException(429,"Rate limit exceeded")
        q.append(now)

def verify_key(x_api_key:str|None=Header(default=None,alias="X-API-Key")):
    if not x_api_key: raise HTTPException(401,"API key required")
    row=key_store.validate_key(cfg.keys_db_path,x_api_key,record_use=False)
    if not row: raise HTTPException(401,"Invalid, disabled or expired API key")
    rate_limit(row["id"]); key_store.validate_key(cfg.keys_db_path,x_api_key,record_use=True); return row

def ensure_ready():
    meta=lookup_store.import_meta(cfg.db_path)
    if not meta["exists"]: raise HTTPException(503,"Lookup database not built yet")
    if not meta["completed"] and not cfg.allow_lookup_during_import: raise HTTPException(503,"Lookup import not complete")
    return meta

@app.get("/health")
def health(): return {"status":"ok","database":lookup_store.import_meta(cfg.db_path),"full_fields":cfg.show_full_fields}

@app.get("/lookup")
def lookup(number:str=Query(...,min_length=7,max_length=32), key=Depends(verify_key)):
    ensure_ready()
    try: rows=lookup_store.lookup_number(cfg.db_path,number,cfg.max_results,cfg.show_full_fields)
    except ValueError as e: raise HTTPException(400,str(e))
    return {"found":bool(rows),"number":lookup_store.normalize_number(number),"count":len(rows),"results":rows}

@app.get("/user/{mobile}")
def user(mobile:str,key=Depends(verify_key)):
    ensure_ready()
    try: rows=lookup_store.lookup_number(cfg.db_path,mobile,cfg.max_results,cfg.show_full_fields)
    except ValueError as e: raise HTTPException(400,str(e))
    if not rows: raise HTTPException(404,"User not found")
    payload=dict(rows[0]); payload["count"]=len(rows); payload["results"]=rows; return payload

@app.get("/id/{record_id}")
def by_id(record_id:str,key=Depends(verify_key)):
    ensure_ready(); rows=lookup_store.lookup_id(cfg.db_path,record_id,cfg.max_results,cfg.show_full_fields)
    if not rows: raise HTTPException(404,"Record not found")
    return {"found":True,"id":record_id,"count":len(rows),"results":rows}

@app.get("/stats")
def stats(key=Depends(verify_key)): return {"database":lookup_store.import_meta(cfg.db_path),"api_keys":key_store.stats(cfg.keys_db_path)}
