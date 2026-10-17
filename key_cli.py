#!/usr/bin/env python3
import argparse
from config import load_settings
import key_store
cfg=load_settings(); key_store.init_db(cfg.keys_db_path)
ap=argparse.ArgumentParser(); sp=ap.add_subparsers(dest="cmd",required=True)
g=sp.add_parser("generate"); g.add_argument("name"); g.add_argument("--days",type=int,default=0)
sp.add_parser("list").add_argument("--limit",type=int,default=100)
for c in ("enable","disable","delete","info"):
    p=sp.add_parser(c); p.add_argument("id",type=int)
r=sp.add_parser("rename"); r.add_argument("id",type=int); r.add_argument("name")
e=sp.add_parser("expire"); e.add_argument("id",type=int); e.add_argument("days")
a=ap.parse_args()
if a.cmd=="generate":
    d=key_store.generate_key(cfg.keys_db_path,a.name,None,a.days or None); print("ID:",d["id"]); print("KEY:",d["key"]); print("EXPIRES:",d["expires_at"] or "Never")
elif a.cmd=="list":
    for x in key_store.list_keys(cfg.keys_db_path,a.limit): print(x)
elif a.cmd=="info": print(key_store.get_key(cfg.keys_db_path,a.id))
elif a.cmd=="enable": print(key_store.set_enabled(cfg.keys_db_path,a.id,True))
elif a.cmd=="disable": print(key_store.set_enabled(cfg.keys_db_path,a.id,False))
elif a.cmd=="delete": print(key_store.delete_key(cfg.keys_db_path,a.id))
elif a.cmd=="rename": print(key_store.rename_key(cfg.keys_db_path,a.id,a.name))
elif a.cmd=="expire": print(key_store.set_expiry(cfg.keys_db_path,a.id,None if a.days.lower()=="never" else int(a.days.rstrip("d"))))
