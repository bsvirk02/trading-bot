#!/bin/bash
#
# launchd entry point for the daily BTC signal run.
#
# Replaces a cron entry that pointed at ~/Desktop/Trading, a directory that no
# longer exists. Because the redirect target directory was also missing, cron
# could not even write the error - the job failed completely silently from
# 2 June until 9 September.
#
# Three things prevent a repeat:
#   - dated log files, so `ls logs/` shows at a glance when it last ran
#   - launchd's own StandardErrorPath, which catches failures occurring before
#     this script can redirect anything
#   - a Pushover alert on any non-zero exit (below), because this bot is
#     otherwise silent unless it has a signal to report, which makes a dead
#     scheduler and a quiet market look identical
set -u

DIR="/Users/bhavdeepvirk/Trading Bot Project"
cd "$DIR" || { echo "cannot cd to $DIR"; exit 1; }

mkdir -p logs
LOG="logs/run_$(date +%Y-%m-%d).log"

# Not `exec`: the shell must survive the python process to inspect its exit
# code and alert. That is the whole point of this wrapper.
/usr/bin/python3 -u live_bot.py >> "$LOG" 2>&1
STATUS=$?

if [ $STATUS -ne 0 ]; then
    # Read the bot's own Pushover credentials from settings.py (.env-backed).
    # Note this project uses PUSHOVER_API_TOKEN, not the job agent's
    # PUSHOVER_APP_TOKEN - the two codebases named it differently.
    TAIL=$(tail -c 400 "$LOG" 2>/dev/null | tr -d '\000')
    /usr/bin/python3 - "$STATUS" "$TAIL" <<'PY' || echo "alert failed too"
import sys, urllib.parse, urllib.request
sys.path.insert(0, "/Users/bhavdeepvirk/Trading Bot Project")
try:
    from settings import PUSHOVER_USER_KEY, PUSHOVER_API_TOKEN
except Exception as e:
    print(f"cannot load Pushover keys: {e}")
    sys.exit(1)

status, tail = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
data = urllib.parse.urlencode({
    "token": PUSHOVER_API_TOKEN,
    "user": PUSHOVER_USER_KEY,
    "title": f"Trading bot FAILED (exit {status})",
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
    print(f"failure alert could not be sent: {e}")
PY
fi

exit $STATUS
