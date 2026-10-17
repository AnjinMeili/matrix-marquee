#!/bin/sh
set -eu

usage() {
    cat <<'EOF'
Usage: install.sh [--source DIR] [--skip-os-packages] [--no-start]

Non-interactively install the current source tree on DietPi. Must run as root.
The existing /etc/protest/protest.env is always preserved.
EOF
}

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
INSTALL_PACKAGES=1
START_SERVICE=1
while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) [ "$#" -ge 2 ] || { usage >&2; exit 2; }; SOURCE_DIR=$2; shift 2 ;;
        --skip-os-packages) INSTALL_PACKAGES=0; shift ;;
        --no-start) START_SERVICE=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "install.sh must run as root" >&2; exit 1; }
[ -f "$SOURCE_DIR/.env.example" ] || { echo "invalid source: $SOURCE_DIR" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

if [ "$INSTALL_PACKAGES" -eq 1 ]; then
    apt-get update -q
    apt-get install -y --no-install-recommends python3 python3-venv python3-pip curl rsync logrotate \
        ffmpeg fonts-dejavu-core avahi-daemon
fi

install -d -m 0755 /opt/protest /etc/protest /var/lib/protest /var/log/protest
release="/opt/protest/releases/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0755 "$release"
rsync -a --delete \
    --exclude='.git/' --exclude='.env' --exclude='*.env' --exclude='.venv/' \
    --exclude='.pytest_cache/' --exclude='__pycache__/' \
    --exclude='backups/' --exclude='bundles/' "$SOURCE_DIR/" "$release/"

python3 -m venv "$release/.venv"
if [ -f "$release/requirements.txt" ]; then
    "$release/.venv/bin/pip" install --disable-pip-version-check --no-input -r "$release/requirements.txt"
elif [ -f "$release/pyproject.toml" ]; then
    "$release/.venv/bin/pip" install --disable-pip-version-check --no-input "$release"
else
    echo "warning: no Python dependency manifest found; service may not start" >&2
fi

# The rgbmatrix bindings are built on-device (see scripts/build-rgbmatrix.sh) and
# cached as a wheel, because each release gets a fresh virtualenv.
if ls /opt/protest/wheels/rgbmatrix-*.whl >/dev/null 2>&1; then
    "$release/.venv/bin/pip" install --disable-pip-version-check --no-input \
        /opt/protest/wheels/rgbmatrix-*.whl
else
    echo "warning: no rgbmatrix wheel in /opt/protest/wheels; run scripts/build-rgbmatrix.sh," \
        "or the matrix is reported unavailable in hardware mode" >&2
fi

if [ ! -e /etc/protest/protest.env ]; then
    install -m 0600 "$release/.env.example" /etc/protest/protest.env
fi
chmod 0600 /etc/protest/protest.env
ln -sfn "$release" /opt/protest/current

install -m 0644 "$release/deploy/systemd/protest.service" /etc/systemd/system/protest.service
install -m 0644 "$release/deploy/systemd/protest-health.service" /etc/systemd/system/protest-health.service
install -m 0644 "$release/deploy/systemd/protest-health.timer" /etc/systemd/system/protest-health.timer
install -d -m 0755 /etc/systemd/journald.conf.d
install -m 0644 "$release/deploy/journald/60-protest-persistent.conf" \
    /etc/systemd/journald.conf.d/60-protest-persistent.conf
install -m 0644 "$release/deploy/logrotate/protest" /etc/logrotate.d/protest
# Announce the sign as <hostname>.local on every network it joins.
if [ -d /etc/avahi/services ]; then
    install -m 0644 "$release/deploy/avahi/protest.service" /etc/avahi/services/protest.service
    systemctl enable avahi-daemon >/dev/null 2>&1 || true
    systemctl restart avahi-daemon || echo "warning: avahi-daemon did not start; protest.local will not resolve" >&2
fi
install -d -m 2755 /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal
systemctl restart systemd-journald
systemctl daemon-reload
systemctl enable protest.service protest-health.timer
if [ "$START_SERVICE" -eq 1 ]; then
    systemctl restart protest.service
    systemctl start protest-health.timer
fi
# Keep the newest releases for rollback and drop the rest so the SD card does
# not fill up; the active release is never removed.
KEEP_RELEASES=${KEEP_RELEASES:-5}
current=$(readlink -f /opt/protest/current)
ls -1d /opt/protest/releases/*/ 2>/dev/null | sed 's:/$::' | sort -r | tail -n +"$((KEEP_RELEASES + 1))" |
    while IFS= read -r old; do
        [ "$(readlink -f "$old")" = "$current" ] || rm -rf -- "$old"
    done
echo "installed release $release"

