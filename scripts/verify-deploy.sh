#!/bin/sh
# Check a deployed sign end to end and print a short report.
#
# Usage: scripts/verify-deploy.sh [--host HOST] [--since "5 min ago"]
#
# Read-only: it changes nothing on the sign. Exits non-zero if any check fails,
# so it can gate a deploy. Checks: every page and API the UI loads answers 200,
# the service is active with no restarts, no errors or tracebacks in the
# service journal since --since, the sign is playing without skipped items, and
# the Pi reports no under-voltage or slow-down since boot.
set -eu

HOST=${PROTEST_DEPLOY_HOST:-protest.local}
SINCE="5 min ago"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --host) HOST=$2; shift 2 ;;
        --since) SINCE=$2; shift 2 ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
case "$HOST" in *[!A-Za-z0-9.:-]*) echo "invalid host" >&2; exit 2;; esac

BASE="http://$HOST"
SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 root@$HOST"
failures=0
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }
ok() { echo "ok    $*"; }

# The service restarts during a deploy; give it a moment to come back.
tries=0
until curl -fs -m 3 -o /dev/null "$BASE/health"; do
    tries=$((tries + 1))
    [ "$tries" -ge 30 ] && { fail "$BASE/health did not answer within 60 s"; exit 1; }
    sleep 2
done

for endpoint in / /static/app.js /static/app.css /api/status /api/network /api/playlists \
    /api/media /api/media/tags /api/text-panels /api/text-panels/fonts /api/diagnostics \
    /api/health /api/display /api/usage /api/playback/state /api/playback/frame; do
    code=$(curl -s -m 15 -o /dev/null -w "%{http_code}" "$BASE$endpoint")
    [ "$code" = 200 ] || fail "$endpoint answered $code"
done
[ "$failures" -eq 0 ] && ok "all pages and APIs answer 200"

state=$(curl -fs -m 10 "$BASE/api/playback/state")
summary=$(printf '%s' "$state" | python3 -c '
import json, sys
s = json.load(sys.stdin)
label = s.get("source_label") or "-"
if s.get("position") is not None:
    label = "%s, item %s of %s" % (label, s["position"] + 1, s["item_count"])
warnings = "; ".join(s.get("warnings") or []) or "no skipped items"
print(" | ".join(part for part in (s["status"], label, warnings, s.get("error")) if part))
')
case "$summary" in
    playing*"no skipped items"*) ok "sign: $summary" ;;
    *) fail "sign: $summary" ;;
esac

remote=$($SSH "
    echo \"release=\$(basename \"\$(readlink /opt/protest/current)\")\"
    echo \"active=\$(systemctl is-active protest)\"
    echo \"restarts=\$(systemctl show protest -p NRestarts --value)\"
    echo \"errors=\$(journalctl -u protest --since '$SINCE' --no-pager -o cat | grep -cE '\"level\":\"ERROR\"|Traceback' || true)\"
    echo \"throttled=\$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)\"
    echo \"temp=\$(vcgencmd measure_temp 2>/dev/null | cut -d= -f2)\"
") || { fail "ssh root@$HOST failed"; remote=""; }

value() { printf '%s\n' "$remote" | sed -n "s/^$1=//p"; }
release=$(value release)
[ "$(value active)" = active ] && ok "service active, release $release" || fail "service is $(value active)"
[ "$(value restarts)" = 0 ] && ok "no service restarts" || fail "service restarted $(value restarts) time(s)"
[ "$(value errors)" = 0 ] && ok "no errors in the journal since $SINCE" || fail "$(value errors) error line(s) in the journal since $SINCE (journalctl -u protest --since '$SINCE')"
case "$(value throttled)" in
    0x0) ok "no under-voltage or slow-down since boot, $(value temp)" ;;
    "") echo "note  could not read power flags" ;;
    *) fail "power/heat flags $(value throttled) since boot (see Settings -> Heat & power), $(value temp)" ;;
esac

if [ "$failures" -eq 0 ]; then
    echo "PASS  $HOST release $release"
else
    echo "FAILED $failures check(s) on $HOST"
    exit 1
fi
