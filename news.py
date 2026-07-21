import logging
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def is_high_impact_news():
    try:
        response = requests.get(NEWS_URL, timeout=10)
        response.raise_for_status()
        events = response.json()

        # BUG FIX: agar feed kabhi error object (dict) ya kuch aur return
        # kare to neeche wala loop crash karta tha. List na ho to safe skip.
        if not isinstance(events, list):
            logger.warning(f"[NEWS] Unexpected feed format: {type(events).__name__}")
            return False

        # BUG FIX: datetime.utcnow() Python 3.12+ mein deprecated hai aur
        # future versions mein remove ho sakta hai. datetime.now(timezone.utc)
        # use karna sahi tarika hai. tzinfo strip karke naive UTC datetime
        # banate hain taaki event_time ke saath comparison consistent rahe.
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for event in events:

            if event.get("impact") != "High":
                continue

            if event.get("currency") != "USD":
                continue

            date_str = event.get("date")

            if not date_str:
                continue

            event_time = datetime.fromisoformat(
                date_str.replace("Z", "+00:00")
            )

            # Feed ke timestamps mein real UTC offset hota hai (sirf Z nahi)
            # - tzinfo convert karke naive UTC mein laate hain.
            if event_time.tzinfo is not None:
                event_time = event_time.astimezone(timezone.utc).replace(tzinfo=None)

            # News से 30 मिनट पहले और 30 मिनट बाद
            if abs((event_time - now).total_seconds()) <= 1800:
                return True

        return False

    except Exception as e:
        # BUG FIX: print() rotating log file mein nahi jaata tha —
        # logger use karna consistent hai (logger_setup.py ke saath).
        logger.error(f"[NEWS ERROR] {e}")
        return False
