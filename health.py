"""
Dead-man's switch.

Pushover reports a run that failed. Nothing reports a run that never started:
on 20-21 September the scheduler dropped two days and no alert existed to
notice, because from the machine's point of view nothing had gone wrong.

healthchecks.io expects a ping every day. If one does not arrive within the
grace period it emails you. It works precisely because it runs somewhere else
-- a watchdog on the same machine as the thing it watches cannot detect that
machine being switched off.

Entirely optional: with HEALTHCHECK_URL unset, every function here is a no-op.
"""

import requests

import settings

TIMEOUT = 10


def _ping(suffix="", payload=None):
    if not settings.HEALTHCHECK_URL:
        return False
    url = settings.HEALTHCHECK_URL.rstrip("/") + suffix
    try:
        requests.post(url, data=(payload or "")[:10000], timeout=TIMEOUT)
        return True
    except Exception as exc:
        # Never let monitoring break the thing it monitors.
        print("healthcheck ping failed: {}".format(exc))
        return False


def start():
    """Tell the service a run has begun, so it can measure duration."""
    return _ping("/start")


def success(message=""):
    return _ping("", message)


def failure(message=""):
    """Flags the check as down immediately rather than waiting for silence."""
    return _ping("/fail", message)


if __name__ == "__main__":
    if not settings.HEALTHCHECK_URL:
        raise SystemExit("HEALTHCHECK_URL is not set in .env - nothing to test.")
    print("pinging {} ...".format(settings.HEALTHCHECK_URL))
    ok = success("manual test ping")
    print("ping sent" if ok else "ping failed")
    raise SystemExit(0 if ok else 1)
