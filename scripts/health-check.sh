#!/bin/sh
set -eu

: "${PROTEST_HEALTH_URL:=http://127.0.0.1:80/health}"
: "${HEALTH_TIMEOUT:=5}"
: "${HEALTH_RETRIES:=3}"
: "${HEALTH_RETRY_DELAY:=5}"

case "$PROTEST_HEALTH_URL" in
    http://127.0.0.1:*/*|http://localhost:*/*) ;;
    *)
        echo "health URL must use local HTTP: $PROTEST_HEALTH_URL" >&2
        exit 2
        ;;
esac

attempt=1
while : ; do
    if body=$(/usr/bin/curl --silent --show-error --fail --max-time "$HEALTH_TIMEOUT" \
        --header 'Accept: application/json' "$PROTEST_HEALTH_URL"); then
        break
    fi
    if [ "$attempt" -ge "$HEALTH_RETRIES" ]; then
        echo "health endpoint unreachable after $attempt attempts" >&2
        exit 7
    fi
    attempt=$((attempt + 1))
    sleep "$HEALTH_RETRY_DELAY"
done
case "$body" in
    *'"status"'*'"ok"'*|*'"ok"'*:*true*|*healthy*) ;;
    *)
        echo "health endpoint returned an unexpected response" >&2
        exit 1
        ;;
esac
printf '%s\n' "MatrixMarquee health check passed"
