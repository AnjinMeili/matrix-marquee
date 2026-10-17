#!/bin/sh
set -eu

: "${PROTEST_HOST:=0.0.0.0}"
: "${PROTEST_PORT:=8000}"
: "${PROTEST_APP_MODULE:=matrix_player.app:app}"
: "${PROTEST_WORKERS:=1}"

cd /opt/protest/current
if [ -x .venv/bin/uvicorn ]; then
    exec .venv/bin/uvicorn "$PROTEST_APP_MODULE" \
        --host "$PROTEST_HOST" --port "$PROTEST_PORT" --workers "$PROTEST_WORKERS"
fi
echo "uvicorn is not installed in /opt/protest/current/.venv" >&2
exit 127
