#!/bin/sh
set -eu

usage() {
    cat <<'EOF'
Usage: deploy.sh [--host HOST] [--user USER] [--port PORT] [--identity FILE]
                 [--skip-os-packages] [--no-start]

Default target is root@192.168.1.194. Uses SSH batch mode and never transfers
.env, secrets, uploaded content, runtime data, or local build artifacts.
EOF
}

HOST=${PROTEST_DEPLOY_HOST:-192.168.1.194}
USER=${PROTEST_DEPLOY_USER:-root}
PORT=${PROTEST_DEPLOY_PORT:-22}
IDENTITY=
INSTALL_ARGS=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --host) HOST=$2; shift 2 ;;
        --user) USER=$2; shift 2 ;;
        --port) PORT=$2; shift 2 ;;
        --identity) IDENTITY=$2; shift 2 ;;
        --skip-os-packages) INSTALL_ARGS="$INSTALL_ARGS --skip-os-packages"; shift ;;
        --no-start) INSTALL_ARGS="$INSTALL_ARGS --no-start"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
case "$HOST" in *[!A-Za-z0-9.:-]*) echo "invalid host" >&2; exit 2;; esac
case "$PORT" in *[!0-9]*|'') echo "invalid port" >&2; exit 2;; esac
[ "$USER" = root ] || { echo "DietPi deployment requires --user root" >&2; exit 2; }

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SSH_ARGS="-o BatchMode=yes -o StrictHostKeyChecking=yes -p $PORT"
if [ -n "$IDENTITY" ]; then
    [ -r "$IDENTITY" ] || { echo "identity file is not readable" >&2; exit 2; }
    SSH_ARGS="$SSH_ARGS -i $IDENTITY"
fi
REMOTE="/root/protest-deploy"
# Intentional word splitting supplies individually validated ssh options.
# shellcheck disable=SC2086
rsync -az --delete -e "ssh $SSH_ARGS" \
    --exclude='.git/' --exclude='.env' --exclude='*.env' --exclude='.venv/' \
    --exclude='backups/' --exclude='bundles/' --exclude='media/' \
    "$ROOT/" "$USER@$HOST:$REMOTE/"
# shellcheck disable=SC2086
ssh $SSH_ARGS "$USER@$HOST" "$REMOTE/scripts/install.sh --source $REMOTE $INSTALL_ARGS"
