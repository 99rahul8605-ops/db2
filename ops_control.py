"""Constrained local process management. No arbitrary shell commands from Telegram."""
import os
from contextlib import closing
import signal
import sqlite3
import subprocess
import time
from pathlib import Path
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env')
RUNTIME = BASE / 'ops_runtime'
RUNTIME.mkdir(exist_ok=True)
INDEX_LOG = Path(os.environ.get('INDEX_RECOVERY_LOG', '/root/index_recovery.log'))
TUNNEL_CONFIG = Path(os.environ.get('TUNNEL_CONFIG', '/root/.cloudflared/db2-api.yml'))
CLOUDFLARED = os.environ.get('CLOUDFLARED_BIN', 'cloudflared')


def _pid_file(name):
    return RUNTIME / (name + '.pid')


def _alive(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().split()
        return stat[2] != 'Z'
    except (OSError, IndexError, ValueError):
        return False


def owned_pid(name):
    f = _pid_file(name)
    try:
        pid = int(f.read_text().strip())
        # Verify command line to avoid killing an unrelated reused PID.
        cmd = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
        marker = 'recover_indexes_lowram.py' if name == 'index' else str(TUNNEL_CONFIG)
        if _alive(pid) and marker in cmd:
            return pid
    except (OSError, ValueError):
        pass
    return None


def matches(marker):
    """Return actual worker PIDs, not parent screen/bash wrappers."""
    output = []
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():
            continue
        try:
            pid = int(directory.name)
            argv = [x.decode(errors='replace') for x in (directory / 'cmdline').read_bytes().split(b'\0') if x]
            if not argv or not _alive(pid):
                continue
            executable = Path(argv[0]).name.lower()
            if marker.endswith('.py') and not executable.startswith('python'):
                continue
            if marker == str(TUNNEL_CONFIG) and not executable.startswith('cloudflared'):
                continue
            if marker.endswith('.py'):
                found = any(Path(a).name == Path(marker).name for a in argv[1:])
            else:
                found = marker in argv[1:]
            if found:
                output.append(pid)
        except (OSError, ValueError):
            pass
    return sorted(output)


def start(name, command, log_path, marker):
    found = matches(marker)
    if found:
        return 'already running', found[0]
    if owned_pid(name):
        return 'already running', owned_pid(name)
    with open(log_path, 'ab', buffering=0) as output:
        p = subprocess.Popen(command, cwd=BASE, stdin=subprocess.DEVNULL,
                             stdout=output, stderr=subprocess.STDOUT,
                             start_new_session=True, close_fds=True)
    _pid_file(name).write_text(str(p.pid))
    time.sleep(0.4)
    if not _alive(p.pid):
        raise RuntimeError('Process exited immediately. Check logs.')
    return 'started', p.pid


def stop(name):
    pid = owned_pid(name)
    if not pid:
        raise RuntimeError('Not managed by the bot. Refusing to kill externally started process.')
    os.kill(pid, signal.SIGTERM)
    _pid_file(name).unlink(missing_ok=True)
    return pid


def index_status(db, import_log, log_tail):
    names = ('idx_users_mobile', 'idx_users_alt', 'idx_users_extid')
    present = None
    ready = 'unavailable (database locked or missing)'
    problem = None
    if db.is_file():
        try:
            with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=0.5)) as con:
                present = {row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='users'")}
                meta = dict(con.execute('SELECT key,value FROM import_meta').fetchall())
                ready = str(meta.get('phase') == 'ready' and meta.get('completed') == '1')
        except sqlite3.Error as e:
            problem = str(e)
    else:
        problem = 'database file missing'
    pids = matches('recover_indexes_lowram.py')
    # Prefer fresh log, but database metadata is the authority for DONE statuses.
    files = [file for file in (INDEX_LOG, import_log) if file.is_file()]
    latest = max(files, key=lambda file: file.stat().st_mtime) if files else None
    out = ['📊 Index recovery',
           f'Python PID: {pids[0] if pids else "not running"}',
           f'Database ready: {ready}']
    if present is None:
        out.append('Index counts: currently unavailable (no reliable percentage while DB locked)')
        if problem:
            out.append(f'SQLite: {problem[:160]}')
    else:
        out.append(f'Completed: {sum(n in present for n in names)}/3')
        out.extend(f'{"✅ DONE" if n in present else "⏳ PENDING"} {n}' for n in names)
    if latest:
        out.append(f'\nLatest log: {latest}\n{log_tail(latest, 14, 1900)}')
    return '\n'.join(out)[:3800]


def process_io(pid):
    d = {}
    for line in Path(f'/proc/{pid}/io').read_text().splitlines():
        key, _, val = line.partition(':')
        if key in ('read_bytes', 'write_bytes'):
            d[key] = int(val.strip())
    return d
