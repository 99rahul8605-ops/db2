#!/usr/bin/env python3
import logging, re
from functools import wraps
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import load_settings
import key_store, lookup_store

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",level=logging.INFO)
log=logging.getLogger("lookup-manager-bot"); cfg=load_settings(require_bot=True); key_store.init_db(cfg.keys_db_path)

def admin_only(fn):
    @wraps(fn)
    async def wrap(update:Update,context:ContextTypes.DEFAULT_TYPE):
        uid=update.effective_user.id if update.effective_user else None
        if uid not in cfg.admin_ids:
            if update.effective_message: await update.effective_message.reply_text("⛔ Admin only.")
            return
        return await fn(update,context)
    return wrap

def fmt_record(r,n):
    fields=[("_id",r.get("_id")),("Mobile",r.get("mobile")),("Name",r.get("name")),("Father",r.get("fname")),("Address",r.get("address")),("Alt",r.get("alt")),("Circle",r.get("circle")),("ID",r.get("id")),("Email",r.get("email"))]
    return "\n".join([f"#{n}"]+[f"{k}: {v}" for k,v in fields if v not in (None,"")])

@admin_only
async def start(update,context):
    await update.message.reply_text("🔐 Lookup + API Key Manager\n\n/genkey <name> [days|never]\n/keys [limit]\n/key <id>\n/disable <id>\n/enable <id>\n/rename <id> <name>\n/expire <id> <days|never>\n/delete <id> CONFIRM\n\n/lookup <number>\n/id <record_id>\n/stats\n/health\n/whoami")

@admin_only
async def whoami(update,context): await update.message.reply_text(f"Your Telegram ID: {update.effective_user.id}")

@admin_only
async def genkey(update,context):
    if not context.args: await update.message.reply_text("Usage: /genkey <name> [days|never]"); return
    parts=context.args[:]; days=None; last=parts[-1].lower()
    if last=="never": parts=parts[:-1]
    elif re.fullmatch(r"\d+d?",last): days=int(last.rstrip("d")); parts=parts[:-1]
    name=" ".join(parts).strip()
    if not name: await update.message.reply_text("Key name required."); return
    d=key_store.generate_key(cfg.keys_db_path,name,update.effective_user.id,days)
    await update.message.reply_text(f"✅ API key generated\n\nID: {d['id']}\nName: {d['name']}\nExpires: {d['expires_at'] or 'Never'}\n\nKEY:\n{d['key']}\n\nSave it now. Raw key cannot be shown again.")

@admin_only
async def keys(update,context):
    limit=50
    if context.args:
        try: limit=max(1,min(int(context.args[0]),200))
        except ValueError: pass
    rows=key_store.list_keys(cfg.keys_db_path,limit)
    if not rows: await update.message.reply_text("No API keys."); return
    blocks=[f"{'🟢' if r['enabled'] else '🔴'} #{r['id']} {r['name']}\n{r['key_prefix']}… | uses={r['use_count']} | exp={r['expires_at'] or 'Never'}" for r in rows]
    text="🔐 API Keys\n\n"+"\n\n".join(blocks)
    for i in range(0,len(text),3800): await update.message.reply_text(text[i:i+3800])

@admin_only
async def keyinfo(update,context):
    try: kid=int(context.args[0])
    except Exception: await update.message.reply_text("Usage: /key <id>"); return
    r=key_store.get_key(cfg.keys_db_path,kid)
    if not r: await update.message.reply_text("Key not found."); return
    await update.message.reply_text(f"Key #{r['id']}\nName: {r['name']}\nStatus: {'Enabled' if r['enabled'] else 'Disabled'}\nPrefix: {r['key_prefix']}…\nCreated: {r['created_at']}\nExpires: {r['expires_at'] or 'Never'}\nLast used: {r['last_used_at'] or 'Never'}\nUses: {r['use_count']}")

async def toggle(update,context,enabled):
    try: kid=int(context.args[0])
    except Exception: await update.message.reply_text("Key ID required."); return
    await update.message.reply_text("✅ Updated." if key_store.set_enabled(cfg.keys_db_path,kid,enabled) else "❌ Key not found.")

@admin_only
async def disable(update,context): await toggle(update,context,False)
@admin_only
async def enable(update,context): await toggle(update,context,True)

@admin_only
async def rename(update,context):
    if len(context.args)<2: await update.message.reply_text("Usage: /rename <id> <new name>"); return
    try: kid=int(context.args[0])
    except ValueError: await update.message.reply_text("Invalid ID."); return
    await update.message.reply_text("✅ Renamed." if key_store.rename_key(cfg.keys_db_path,kid," ".join(context.args[1:])) else "❌ Key not found.")

@admin_only
async def expire(update,context):
    if len(context.args)!=2: await update.message.reply_text("Usage: /expire <id> <days|never>"); return
    try: kid=int(context.args[0])
    except ValueError: await update.message.reply_text("Invalid ID."); return
    raw=context.args[1].lower()
    if raw=="never": days=None
    elif re.fullmatch(r"\d+d?",raw): days=int(raw.rstrip("d"))
    else: await update.message.reply_text("Use days or never."); return
    await update.message.reply_text("✅ Expiry updated." if key_store.set_expiry(cfg.keys_db_path,kid,days) else "❌ Key not found.")

@admin_only
async def delete(update,context):
    if len(context.args)<2 or context.args[1].upper()!="CONFIRM": await update.message.reply_text("Usage: /delete <id> CONFIRM"); return
    try: kid=int(context.args[0])
    except ValueError: await update.message.reply_text("Invalid ID."); return
    await update.message.reply_text("✅ Permanently deleted." if key_store.delete_key(cfg.keys_db_path,kid) else "❌ Key not found.")

async def send_rows(update,rows):
    if not rows: await update.message.reply_text("No record found."); return
    text=f"Found {len(rows)} record(s)\n\n"+"\n\n".join(fmt_record(r,i+1) for i,r in enumerate(rows))
    for i in range(0,len(text),3800): await update.message.reply_text(text[i:i+3800])

@admin_only
async def lookup(update,context):
    if not context.args: await update.message.reply_text("Usage: /lookup <number>"); return
    meta=lookup_store.import_meta(cfg.db_path)
    if not meta["exists"] or (not meta["completed"] and not cfg.allow_lookup_during_import): await update.message.reply_text(f"Database not ready: {meta}"); return
    try: rows=lookup_store.lookup_number(cfg.db_path,context.args[0],cfg.max_results,True)
    except Exception as e: await update.message.reply_text(f"Lookup error: {e}"); return
    await send_rows(update,rows)

@admin_only
async def byid(update,context):
    if not context.args: await update.message.reply_text("Usage: /id <record_id>"); return
    try: rows=lookup_store.lookup_id(cfg.db_path,context.args[0],cfg.max_results,True)
    except Exception as e: await update.message.reply_text(f"Lookup error: {e}"); return
    await send_rows(update,rows)

@admin_only
async def stats(update,context):
    meta=lookup_store.import_meta(cfg.db_path); ks=key_store.stats(cfg.keys_db_path); size=cfg.db_path.stat().st_size/1024**3 if cfg.db_path.exists() else 0
    await update.message.reply_text(f"Database: {'ready' if meta.get('completed') else 'not ready'}\nMode: {meta.get('mode')}\nProcessed: {meta.get('processed_records',0):,}\nRows: {meta.get('indexed_rows',0):,}\nDB size: {size:.2f} GiB\n\nAPI keys: {ks['total']} total / {ks['active']} active\nAPI uses: {ks['uses']}")

@admin_only
async def health(update,context): await update.message.reply_text(str(lookup_store.import_meta(cfg.db_path)))

async def err(update,context): log.exception("Unhandled bot error",exc_info=context.error)

def main():
    app=Application.builder().token(cfg.manager_bot_token).build()
    mapping={"start":start,"help":start,"whoami":whoami,"genkey":genkey,"keys":keys,"key":keyinfo,"disable":disable,"revoke":disable,"enable":enable,"rename":rename,"expire":expire,"delete":delete,"lookup":lookup,"id":byid,"stats":stats,"health":health}
    for name,fn in mapping.items(): app.add_handler(CommandHandler(name,fn))
    app.add_error_handler(err); log.info("Manager bot starting..."); app.run_polling(drop_pending_updates=False)

if __name__=="__main__": main()
