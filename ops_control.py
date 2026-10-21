"""Constrained local process management. No arbitrary shell commands from Telegram."""
import os
import signal
import subprocess
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
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
    output = []
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():
            continue
        try:
            pid = int(directory.name)
            cmd = (directory / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
            if marker in cmd and _alive(pid):
                output.append(pid)
        except (OSError, ValueError):
            pass
    return output


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


def index_status(db, meta, log_tail):
    log = log_tail(INDEX_LOG, 20, 2800)
    done = [n for n in ('idx_users_mobile', 'idx_users_alt', 'idx_users_extid') if f'DONE {n}' in log or (INDEX_LOG.exists() and f'DONE {n}' in INDEX_LOG.read_text(errors='replace'))]
    pids = matches('recover_indexes_lowram.py')
    return (f'Indexes completed in log: {len(done)}/3\n'
            f'PID: {pids[0] if pids else "not running"}\n'
            f'Database ready: {meta.get("completed") == "1" and meta.get("phase") == "ready"}\n\n{log}')


def process_io(pid):
    d = {}
    for line in Path(f'/proc/{pid}/io').read_text().splitlines():
        key, _, val = line.partition(':')
        if key in ('read_bytes', 'write_bytes'):
            d[key] = int(val.strip())
    return d
