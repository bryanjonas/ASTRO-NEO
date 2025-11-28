#!/bin/bash
set -e

# Configuration
BACKUP_DIR="/data/backups"
RETENTION_DAYS=7
DATE=$(date +%Y-%m-%d)
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
FILENAME="astro-neo-backup-${TIMESTAMP}.sql.gz"

# Ensure backup directory exists
mkdir -p "${BACKUP_DIR}"

echo "[${TIMESTAMP}] Starting backup..."

# Perform backup
# We assume PGPASSWORD is set in the environment or .pgpass is available
# In docker-compose, we'll set PGPASSWORD env var for the cron container
if pg_dump -h db -U astro astro | gzip > "${BACKUP_DIR}/${FILENAME}"; then
    echo "[${TIMESTAMP}] Backup successful: ${FILENAME}"
    
    # Check file size
    SIZE=$(du -h "${BACKUP_DIR}/${FILENAME}" | cut -f1)
    echo "[${TIMESTAMP}] Backup size: ${SIZE}"
else
    echo "[${TIMESTAMP}] Backup failed!"
    exit 1
fi

# Cleanup old backups
echo "[${TIMESTAMP}] Cleaning up backups older than ${RETENTION_DAYS} days..."
find "${BACKUP_DIR}" -name "astro-neo-backup-*.sql.gz" -mtime +${RETENTION_DAYS} -delete

echo "[${TIMESTAMP}] Backup process complete."
