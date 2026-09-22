"""
Pushover notifications.

This module is the ONLY place that sends notifications. live_bot.py used to
carry its own copy that never checked the response, so it printed
"Notification sent" whether or not Pushover accepted the message -- which is
how a deleted Pushover application went unnoticed.
"""

import requests

import settings

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def send_notification(title, message, priority=0, timeout=15):
    """Send a Pushover message. Returns True only if Pushover accepted it."""
    if not settings.PUSHOVER_USER_KEY or not settings.PUSHOVER_API_TOKEN:
        print("Notification NOT sent: Pushover credentials are not configured.")
        return False

    try:
        response = requests.post(
            PUSHOVER_URL,
            data={
                "token": settings.PUSHOVER_API_TOKEN,
                "user": settings.PUSHOVER_USER_KEY,
                "title": title,
                "message": message,
                "priority": priority,
            },
            timeout=timeout,
        )
    except Exception as exc:
        print("Notification FAILED (network): {}".format(exc))
        return False

    if response.status_code == 200:
        print("Notification sent.")
        return True

    # 4xx from Pushover almost always means a bad/deleted token or user key.
    print(
        "Notification REJECTED by Pushover (HTTP {}): {}".format(
            response.status_code, response.text.strip()[:300]
        )
    )
    return False


if __name__ == "__main__":
    settings.validate()
    ok = send_notification(
        title="Trading Bot Test",
        message="Notifications are working correctly.",
    )
    raise SystemExit(0 if ok else 1)
