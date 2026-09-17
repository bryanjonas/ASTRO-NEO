#!/bin/bash
set -euo pipefail

# Backs up the live SQLite database using SQLite's own online backup API
# (sqlite3.Connection.backup), safe to run concurrently with WAL writers.
# All file operations happen inside the api container because /data on the
# host is root-owned (created by the containers), and the invoking host
# user (bryan) has no write access to it directly -- only via docker exec.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RETENTION_DAYS=14
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
DB_FILENAME="astro-neo-backup-${TIMESTAMP}.db"

cd "${REPO_DIR}"

echo "[${TIMESTAMP}] Starting SQLite backup..."

if docker compose exec -T api python3 -c "
import gzip
import os
import shutil
import sqlite3

os.makedirs('/data/backups', exist_ok=True)
src = sqlite3.connect('/data/astro_neo.db')
dst = sqlite3.connect('/data/backups/${DB_FILENAME}')
src.backup(dst)
dst.close()
src.close()

with open('/data/backups/${DB_FILENAME}', 'rb') as f_in, gzip.open('/data/backups/${DB_FILENAME}.gz', 'wb') as f_out:
    shutil.copyfileobj(f_in, f_out)

os.remove('/data/backups/${DB_FILENAME}')
" 2>&1; then
    SIZE=$(docker compose exec -T api du -h "/data/backups/${DB_FILENAME}.gz" | cut -f1)
    echo "[${TIMESTAMP}] Backup successful: ${DB_FILENAME}.gz (${SIZE})"
else
    echo "[${TIMESTAMP}] Backup failed!" >&2
    exit 1
fi

echo "[${TIMESTAMP}] Cleaning up backups older than ${RETENTION_DAYS} days..."
docker compose exec -T api find /data/backups -name "astro-neo-backup-*.db.gz" -mtime "+${RETENTION_DAYS}" -delete

echo "[${TIMESTAMP}] Backup process complete."
