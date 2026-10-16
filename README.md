# Hitek Upload Bot — Simple Telethon build

Simple Ubuntu/container setup. No systemd, no /opt install required.

## First setup
```bash
cd /root/db2
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp env.example .env
nano .env
./start.sh
```

## Background with screen
```bash
screen -dmS hitekbot bash -lc 'cd /root/db2 && ./start.sh'
screen -r hitekbot
```
Detach: Ctrl+A then D.

## Update from GitHub
```bash
cd /root/db2
git pull
venv/bin/pip install -r requirements.txt
screen -S hitekbot -X quit 2>/dev/null || true
screen -dmS hitekbot bash -lc 'cd /root/db2 && ./start.sh'
```

Commands: /start /ping /whoami /queue /files /parts /disk /test7z /info7z /extract7z CONFIRM

Files are queued and downloaded one-at-a-time. Live download progress shows percentage, speed and ETA.
