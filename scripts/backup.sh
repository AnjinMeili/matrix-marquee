#!/bin/sh
set -eu

: "${BACKUP_DIR:=/var/backups/protest}"
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
install -d -m 0700 "$BACKUP_DIR"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$BACKUP_DIR/protest-config-$stamp.tar.gz"

# Deliberately excludes environment files, credentials, DBs, media, uploads,
# thumbnails, logs, and all user-authored content.
tar -czf "$archive" \
    --exclude='*.env' --exclude='*.db' --exclude='*.sqlite*' \
    --exclude='media' --exclude='uploads' --exclude='thumbnails' \
    --exclude='*.log' --exclude='hostapd/*.conf' \
    /etc/systemd/system/protest.service \
    /etc/systemd/system/protest-health.service \
    /etc/systemd/system/protest-health.timer \
    /etc/systemd/journald.conf.d/60-protest-persistent.conf \
    /etc/logrotate.d/protest \
    /opt/protest/current/deploy \
    2>/dev/null
chmod 0600 "$archive"
echo "$archive"

