
"""
Trading Session Filter (based on UTC time)
London  : 08:00 - 16:00 UTC
New York: 13:00 - 21:00 UTC
Asian   : 00:00 - 08:00 UTC
"""

from datetime import datetime, timezone


def get_current_session():
    # BUG FIX: datetime.utcnow() Python 3.12+ mein deprecated hai
    # (news.py mein fix ho gaya tha lekin yahan reh gaya tha).
    hour = datetime.now(timezone.utc).hour

    london = 8 <= hour < 16
    new_york = 13 <= hour < 21
    asian = 0 <= hour < 8

    if london and new_york:
        return "London + New York Overlap", True
    if london:
        return "London", True
    if new_york:
        return "New York", True
    if asian:
        return "Asian", False

    return "Off-Hours", False
