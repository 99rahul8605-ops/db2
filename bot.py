import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger('hitek-bot')

BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
API_ID = int(os.getenv('TELEGRAM_API_ID', '0') or 0)
API_HASH = os.getenv('TELEGRAM_API_HASH', '').strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()}
UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', str(ROOT / 'uploads'))).resolve()
EXTRACT_DIR = Path(os.getenv('EXTRACT_DIR', str(ROOT / 'extracted'))).resolve()
SESSION_FILE = str((ROOT / 'hitek_bot').resolve())

for p in (UPLOAD_DIR, EXTRACT_DIR):
    p.mkdir(parents=True, exist_ok=True)

if not BOT_TOKEN or not API_ID or not API_HASH or not ADMIN_IDS:
    raise SystemExit('Missing BOT_TOKEN / TELEGRAM_API_ID / TELEGRAM_API_HASH / ADMIN_IDS in .env')

client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
queue = asyncio.Queue()
current = None
operation_lock = asyncio.Lock()
upload_mode = False


def human_size(n):
    n = float(n or 0)
    for u in ('B','KB','MB','GB','TB'):
        if n < 1024 or u == 'TB':
            return f'{n:.2f} {u}'
        n /= 1024


def fmt_eta(sec):
    if not sec or sec <= 0:
        return '--'
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f'{h}h {m}m' if h else (f'{m}m {s}s' if m else f'{s}s')


def safe_name(name):
    name = Path(name or 'file.bin').name
    name = re.sub(r'[^A-Za-z0-9._()\- ]', '_', name)
    return name[:240] or 'file.bin'


def is_admin(event):
    return event.sender_id in ADMIN_IDS


async def admin_only(event):
    if not is_admin(event):
        await event.reply('⛔ Admin only.')
        return False
    return True


def files_list():
    return sorted((p for p in UPLOAD_DIR.iterdir() if p.is_file()), key=lambda p: p.name)


def detect_parts():
    pat = re.compile(r'^(?P<base>.+\.7z)\.(?P<num>\d{3,})$', re.I)
    groups = {}
    for p in files_list():
        m = pat.match(p.name)
        if m:
            groups.setdefault(m.group('base'), []).append((int(m.group('num')), p))
    return groups


def validate_parts(items):
    if not items:
        return False, 'No parts found'
    nums = sorted(n for n, _ in items)
    missing = sorted(set(range(1, nums[-1] + 1)) - set(nums))
    if missing:
        preview = ', '.join(f'.{n:03d}' for n in missing[:25])
        return False, f'Missing {len(missing)} part(s): {preview}' + (' ...' if len(missing)>25 else '')
    return True, f'{len(nums)} contiguous parts (.001-.{nums[-1]:03d})'


def choose_group(require_complete=True):
    groups = detect_parts()
    if not groups:
        raise RuntimeError('No *.7z.001 style archive found.')
    for base, items in sorted(groups.items()):
        ok, msg = validate_parts(items)
        if ok:
            return base, sorted(items)
    base, items = sorted(groups.items())[0]
    if require_complete:
        raise RuntimeError(validate_parts(items)[1])
    return base, sorted(items)


async def run_cmd(args, cwd=None):
    proc = await asyncio.create_subprocess_exec(*args, cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors='replace')


async def edit_safe(msg, text):
    try:
        await msg.edit(text)
        return True
    except Exception as e:
        log.debug('Status edit skipped: %s: %s', type(e).__name__, e)
        return False


def progress_bar(done, total, width=12):
    if not total or total <= 0:
        return '░' * width
    ratio = max(0.0, min(1.0, done / total))
    filled = int(ratio * width)
    return '█' * filled + '░' * (width - filled)


async def download_worker():
    global current
    while True:
        event = await queue.get()
        try:
            msg = event.message
            f = msg.file
            name = safe_name(getattr(f, 'name', None) or f'file_{msg.id}.bin')
            size = int(getattr(f, 'size', 0) or 0)
            dest = UPLOAD_DIR / name
            current = name
            status = await event.reply(
                f'⬇️ Starting `{name}`\n'
                f'Size: {human_size(size)}\n'
                f'Queue waiting: {queue.qsize()}'
            )
            started = time.monotonic()
            state = {'last_update': 0.0, 'last_bytes': 0, 'last_t': started}

            async def progress(done, total):
                now = time.monotonic()
                if total and done < total and (now - state['last_update']) < 4.0:
                    return
                dt = max(now - state['last_t'], 0.001)
                delta = max(done - state['last_bytes'], 0)
                speed = delta / dt if delta else done / max(now - started, 0.001)
                eta = ((total - done) / speed) if total and speed > 0 else 0
                pct = (done * 100 / total) if total else 0.0
                text = (
                    f'⬇️ Downloading `{name}`\n'
                    f'[{progress_bar(done, total)}] {pct:.1f}%\n'
                    f'{human_size(done)} / {human_size(total)}\n'
                    f'Speed: {human_size(speed)}/s • ETA: {fmt_eta(eta)}\n'
                    f'Queue waiting: {queue.qsize()}'
                )
                await edit_safe(status, text)
                state['last_update'] = now
                state['last_bytes'] = done
                state['last_t'] = now

            await client.download_media(msg, file=str(dest), progress_callback=progress)
            elapsed = max(time.monotonic() - started, 0.001)
            avg = dest.stat().st_size / elapsed if dest.exists() else 0
            await edit_safe(
                status,
                f'✅ Downloaded `{name}`\n'
                f'Size: {human_size(dest.stat().st_size)}\n'
                f'Average: {human_size(avg)}/s\n'
                f'Queue waiting: {queue.qsize()}'
            )
        except Exception as e:
            log.exception('Download failed')
            try:
                await event.reply(f'❌ Download failed: {type(e).__name__}: {e}')
            except Exception:
                pass
        finally:
            current = None
            queue.task_done()


@client.on(events.NewMessage)
async def logger(event):
    log.info('INCOMING sender=%s chat=%s text=%r file=%s', event.sender_id, event.chat_id,
             event.raw_text[:120] if event.raw_text else '', bool(event.message.file))


@client.on(events.NewMessage(pattern=r'^/(?:start|help)(?:@\w+)?$'))
async def start_cmd(event):
    if not await admin_only(event): return
    await event.reply(
        '🗄 Hitek Upload Bot\n\n'
        'Upload mode is OFF by default. Use /upload_on before sending files.\n'
        'Use /upload_off when finished. Files sent while OFF are ignored.\n\n'
        '/upload_on - enable file uploads\n/upload_off - disable file uploads\n/upload_status - show upload mode\n'
        '/ping - test bot\n/whoami - IDs\n/queue - queue status\n/files - downloaded files\n'
        '/parts - check .7z parts\n/disk - disk/RAM\n/test7z - test complete archive\n'
        '/info7z - archive info\n/extract7z CONFIRM - extract archive'
    )


@client.on(events.NewMessage(pattern=r'^/ping(?:@\w+)?$'))
async def ping_cmd(event):
    if not await admin_only(event): return
    await event.reply('✅ Pong. Bot is receiving commands.')


@client.on(events.NewMessage(pattern=r'^/whoami(?:@\w+)?$'))
async def whoami_cmd(event):
    await event.reply(f'User ID: `{event.sender_id}`\nChat ID: `{event.chat_id}`\nAdmin: `{event.sender_id in ADMIN_IDS}`')




@client.on(events.NewMessage(pattern=r'^/upload_on(?:@\w+)?$'))
async def upload_on_cmd(event):
    global upload_mode
    if not await admin_only(event): return
    upload_mode = True
    await event.reply('🟢 Upload mode ON. Files you send/forward now will be queued one by one.\nWhen finished, use /upload_off.')


@client.on(events.NewMessage(pattern=r'^/upload_off(?:@\w+)?$'))
async def upload_off_cmd(event):
    global upload_mode
    if not await admin_only(event): return
    upload_mode = False
    await event.reply(f'🔴 Upload mode OFF. New files will be ignored.\nCurrent download: `{current or "-"}`\nWaiting in queue: {queue.qsize()}')


@client.on(events.NewMessage(pattern=r'^/upload_status(?:@\w+)?$'))
async def upload_status_cmd(event):
    if not await admin_only(event): return
    await event.reply(
        f'Upload mode: {"🟢 ON" if upload_mode else "🔴 OFF"}\n'
        f'Current: `{current or "-"}`\nWaiting: {queue.qsize()}'
    )


@client.on(events.NewMessage(pattern=r'^/queue(?:@\w+)?$'))
async def queue_cmd(event):
    if not await admin_only(event): return
    await event.reply(f'Current: `{current or "-"}`\nWaiting: {queue.qsize()}')


@client.on(events.NewMessage(pattern=r'^/files(?:@\w+)?$'))
async def files_cmd(event):
    if not await admin_only(event): return
    fs = files_list()
    if not fs:
        return await event.reply('No downloaded files yet.')
    total = sum(p.stat().st_size for p in fs)
    lines = [f'Files: {len(fs)} • Total: {human_size(total)}']
    lines += [f'{p.name} — {human_size(p.stat().st_size)}' for p in fs[:60]]
    if len(fs)>60: lines.append(f'... +{len(fs)-60} more')
    await event.reply('\n'.join(lines))


@client.on(events.NewMessage(pattern=r'^/parts(?:@\w+)?$'))
async def parts_cmd(event):
    if not await admin_only(event): return
    groups = detect_parts()
    if not groups:
        return await event.reply('No *.7z.001 style parts found.')
    lines=[]
    for base, items in sorted(groups.items()):
        ok, msg = validate_parts(items)
        total=sum(p.stat().st_size for _,p in items)
        lines.append(f'{"✅" if ok else "⚠️"} {base}: {msg}; {human_size(total)}')
    await event.reply('\n'.join(lines))


@client.on(events.NewMessage(pattern=r'^/disk(?:@\w+)?$'))
async def disk_cmd(event):
    if not await admin_only(event): return
    d=shutil.disk_usage(UPLOAD_DIR)
    mem=''
    try:
        with open('/proc/meminfo') as fh: mem='\n'.join(fh.read().splitlines()[:4])
    except: pass
    await event.reply(f'Disk total: {human_size(d.total)}\nDisk used: {human_size(d.used)}\nDisk free: {human_size(d.free)}\n\n{mem}')


@client.on(events.NewMessage(pattern=r'^/test7z(?:@\w+)?$'))
async def test_cmd(event):
    if not await admin_only(event): return
    if operation_lock.locked(): return await event.reply('⚠️ Archive operation already running.')
    try:
        _, items=choose_group(True)
        first=items[0][1]
        m=await event.reply(f'🧪 Testing `{first.name}` ...')
        async with operation_lock:
            code,out=await run_cmd(['7z','t',str(first)])
        tail=out[-3500:]
        await m.edit(f'7z test exit={code}\n```\n{tail}\n```')
    except Exception as e:
        await event.reply(f'❌ {type(e).__name__}: {e}')


@client.on(events.NewMessage(pattern=r'^/info7z(?:@\w+)?$'))
async def info_cmd(event):
    if not await admin_only(event): return
    try:
        _,items=choose_group(True)
        code,out=await run_cmd(['7z','l','-slt',str(items[0][1])])
        await event.reply(f'exit={code}\n```\n{out[-3500:]}\n```')
    except Exception as e:
        await event.reply(f'❌ {type(e).__name__}: {e}')


@client.on(events.NewMessage(pattern=r'^/extract7z(?:@\w+)?(?:\s+CONFIRM)?$'))
async def extract_cmd(event):
    if not await admin_only(event): return
    if 'CONFIRM' not in event.raw_text:
        return await event.reply('Use: `/extract7z CONFIRM`')
    if operation_lock.locked(): return await event.reply('⚠️ Archive operation already running.')
    try:
        _,items=choose_group(True)
        first=items[0][1]
        status=await event.reply(f'📦 Extracting `{first.name}` ...')
        async with operation_lock:
            proc=await asyncio.create_subprocess_exec('7z','x',str(first),f'-o{EXTRACT_DIR}','-y','-bb1','-bsp1',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            last=0.0; tail=''; pct_re=re.compile(r'(\d{1,3})%')
            while True:
                chunk=await proc.stdout.read(4096)
                if not chunk: break
                txt=chunk.decode(errors='replace'); tail=(tail+txt)[-12000:]
                now=time.monotonic()
                if now-last>=4:
                    mm=pct_re.findall(tail.replace('\r','\n'))
                    pct=mm[-1] if mm else 'working'
                    d=shutil.disk_usage(EXTRACT_DIR)
                    await edit_safe(status,f'📦 Extracting `{first.name}`\nProgress: {pct}{"%" if str(pct).isdigit() else ""}\nFree disk: {human_size(d.free)}')
                    last=now
            code=await proc.wait()
        await edit_safe(status, ('✅' if code==0 else '❌')+f' Extraction finished. Exit={code}\nFree disk: {human_size(shutil.disk_usage(EXTRACT_DIR).free)}')
        if code!=0: await event.reply(f'```\n{tail[-3500:]}\n```')
    except Exception as e:
        log.exception('Extraction failed')
        await event.reply(f'❌ {type(e).__name__}: {e}')


@client.on(events.NewMessage(func=lambda e: bool(e.message.file) and not (e.raw_text or '').startswith('/')))
async def file_handler(event):
    if not await admin_only(event): return
    f=event.message.file
    name=safe_name(getattr(f,'name',None) or f'file_{event.message.id}.bin')
    if not upload_mode:
        return await event.reply(
            f'⛔ Upload mode is OFF. `{name}` was NOT queued or downloaded.\n'
            'Use /upload_on first if you want to upload files.'
        )
    await queue.put(event)
    await event.reply(f'📥 Queued `{name}`\nSize: {human_size(getattr(f,"size",0))}\nQueue position: {queue.qsize()}')


async def main():
    await client.start(bot_token=BOT_TOKEN)
    me=await client.get_me()
    log.info('Started as @%s id=%s', me.username, me.id)
    log.info('ADMIN_IDS=%s', sorted(ADMIN_IDS))
    log.info('Upload mode starts OFF for safety')
    worker=asyncio.create_task(download_worker())
    for uid in ADMIN_IDS:
        try:
            await client.send_message(uid, f'✅ @{me.username} started. Send /ping.')
        except Exception as e:
            log.warning('Startup message failed for %s: %s', uid, e)
    try:
        await client.run_until_disconnected()
    finally:
        worker.cancel()


if __name__ == '__main__':
    asyncio.run(main())
