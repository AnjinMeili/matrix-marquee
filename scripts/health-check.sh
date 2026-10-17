#!/bin/sh
set -eu

: "${PROTEST_HEALTH_URL:=http://127.0.0.1:8000/health}"
: "${HEALTH_TIMEOUT:=5}"

case "$PROTEST_HEALTH_URL" in
    http://127.0.0.1:*/*|http://localhost:*/*) ;;
    *)
        echo "health URL must use local HTTP: $PROTEST_HEALTH_URL" >&2
        exit 2
        ;;
esac

body=$(/usr/bin/curl --silent --show-error --fail --max-time "$HEALTH_TIMEOUT" \
    --header 'Accept: application/json' "$PROTEST_HEALTH_URL")
case "$body" in
    *'"status"'*'"ok"'*|*'"ok"'*:*true*|*healthy*) ;;
    *)
        echo "health endpoint returned an unexpected response" >&2
        exit 1
        ;;
esac
printf '%s\n' "protest health check passed"

