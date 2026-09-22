#!/bin/bash
#
# launchd entry point for the daily BTC signal run.
#
# History: this replaced a cron entry pointing at ~/Desktop/Trading, a
# directory that no longer existed. Because the redirect target was also
# missing, cron could not even write the error - the job failed silently from
# 2 June until 9 September.
#
# On 20-21 September it failed again, differently. The Mac was powered off at
# 08:00 (booted 09:00 on the 21st), and launchd does not defer a missed
# StartCalendarInterval window - it discards it and schedules the next one.
# Two days of signals were dropped with no error anywhere, because nothing
# had failed.
#
# Hence the design here:
#   - the run is IDEMPOTENT, guarded by a per-day success marker, so it is
#     safe to fire from several triggers
#   - launchd fires it at boot/login (RunAtLoad), at 08:00, and again at 20:00
#     as a backstop. Whichever fires first does the work; the rest no-op.
#   - dated log files, so `ls logs/` shows at a glance when it last ran
#   - launchd's own StandardErrorPath catches failures occurring before this
#     script can redirect anything
#   - a Pushover alert on any non-zero exit, because this bot is otherwise
#     silent unless it has a signal to report, which makes a dead scheduler
#     and a quiet market look identical
set -u

# Derive the project directory from this script's own location rather than
# hardcoding it. A hardcoded path (~/Desktop/Trading) is exactly what broke
# the original cron job when the directory moved.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || { echo "cannot cd to $DIR"; exit 1; }
export BOT_DIR="$DIR"

mkdir -p logs
TODAY=$(date +%Y-%m-%d)
LOG="logs/run_${TODAY}.log"
STATE="logs/.last_success"

# --- Idempotency guard -------------------------------------------------
# If today's run already succeeded, do nothing. This is what makes it safe
# to trigger at boot, at 08:00 and at 20:00 without sending three
# notifications or placing a trade three times.
if [ "$(cat "$STATE" 2>/dev/null)" = "$TODAY" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') run already succeeded today; skipping." >> "$LOG"
    exit 0
fi

# Prefer the project virtualenv so the bot's dependencies are pinned and
# independent of whatever the system Python happens to have. Falls back to the
# system interpreter if the venv has not been created yet.
if [ -x "$DIR/.venv/bin/python3" ]; then
    PYTHON="$DIR/.venv/bin/python3"
else
    PYTHON="/usr/bin/python3"
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') starting run (trigger: ${1:-unspecified}, python: $PYTHON)" >> "$LOG"

# Not `exec`: the shell must survive the python process to inspect its exit
# code and alert. That is the whole point of this wrapper.
"$PYTHON" -u live_bot.py >> "$LOG" 2>&1
STATUS=$?

if [ $STATUS -eq 0 ]; then
    # Only a fully successful run - signal computed AND notification
    # delivered - counts. A failed notification exits non-zero, so a later
    # trigger today will retry rather than assume the job is done.
    echo "$TODAY" > "$STATE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') run succeeded" >> "$LOG"
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') run FAILED with exit $STATUS" >> "$LOG"

# Read the bot's own Pushover credentials from settings.py (.env-backed)
# rather than duplicating them here.
TAIL=$(tail -c 400 "$LOG" 2>/dev/null | tr -d '\000')
"$PYTHON" - "$STATUS" "$TAIL" <<'PY' || echo "alert failed too" >> "$LOG"
import os, sys, urllib.parse, urllib.request
sys.path.insert(0, os.environ.get("BOT_DIR", os.getcwd()))
try:
    from settings import PUSHOVER_USER_KEY, PUSHOVER_API_TOKEN
except Exception as e:
    print("cannot load Pushover keys: {}".format(e))
    sys.exit(1)

status, tail = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
data = urllib.parse.urlencode({
    "token": PUSHOVER_API_TOKEN,
    "user": PUSHOVER_USER_KEY,
    "title": "Trading bot FAILED (exit {})".format(status),
    "message": (tail or "no output captured")[-400:],
    "priority": 1,
}).encode()
try:
    urllib.request.urlopen(
        urllib.request.Request("https://api.pushover.net/1/messages.json", data=data),
        timeout=15,
    )
    print("failure alert sent")
except Exception as e:
    print("failure alert could not be sent: {}".format(e))
PY

exit $STATUS
