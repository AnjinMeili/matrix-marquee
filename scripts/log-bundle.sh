#!/bin/sh
set -eu

: "${BUNDLE_DIR:=/var/backups/protest}"
: "${LOG_SINCE:=24 hours ago}"
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
install -d -m 0700 "$BUNDLE_DIR"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
work="$BUNDLE_DIR/.bundle-$stamp"
archive="$BUNDLE_DIR/matrix-marquee-logs-$stamp.tar.gz"
install -d -m 0700 "$work"
trap 'rm -rf "$work"' EXIT HUP INT TERM

journalctl --no-pager --since "$LOG_SINCE" \
    -u protest.service -u protest-health.service -u hostapd.service -u dnsmasq.service |
    sed -E \
        -e 's/([Pp]ass(word|phrase)?|token|secret|authorization)[=: ]+[^ ]+/\1=[REDACTED]/g' \
        -e 's/(filename|path|text|content|body)"?[=: ]+"?[^", ]+/\1=[REDACTED]/g' \
        > "$work/journal.txt"
systemctl --no-pager --full status protest.service > "$work/service-status.txt" 2>&1 || true
df -h / /var/lib/protest > "$work/disk.txt" 2>&1 || true
free -h > "$work/memory.txt" 2>&1 || true
uname -a > "$work/system.txt"
ip -brief link > "$work/network-links.txt"
tar -czf "$archive" -C "$work" .
chmod 0600 "$archive"
echo "$archive"
