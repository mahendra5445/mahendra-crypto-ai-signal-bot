import logging
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

_LAST_OK: list | None = None


def _eval_events(events: list) -> bool:
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for event in events:
        if event.get("impact") != "High":
            continue
        if event.get("currency") != "USD":
            continue

        date_str = event.get("date")
        if not date_str:
            continue

        event_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if event_time.tzinfo is not None:
            event_time = event_time.astimezone(timezone.utc).replace(tzinfo=None)

        if abs((event_time - now).total_seconds()) <= 1800:
            return True
    return False


def is_high_impact_news():
    """
    H-011: on feed failure, reuse last-good events; if never hydrated, fail
    closed (return True) so we pause rather than trade blind through news.
    """
    global _LAST_OK
    try:
        response = requests.get(NEWS_URL, timeout=10)
        response.raise_for_status()
        events = response.json()

        if not isinstance(events, list):
            logger.warning(f"[NEWS] Unexpected feed format: {type(events).__name__}")
            if _LAST_OK is not None:
                return _eval_events(_LAST_OK)
            return True

        _LAST_OK = events
        return _eval_events(events)

    except Exception as e:
        logger.error(f"[NEWS ERROR] {e}")
        if _LAST_OK is not None:
            return _eval_events(_LAST_OK)
        return True
