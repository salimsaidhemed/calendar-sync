"""Read-only Google Calendar adapter with all-or-nothing pagination."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
import time
from urllib.parse import quote

import requests

from calendar_sync.google_oauth import GoogleError, GoogleTokens, quiet_network


class GoogleCalendar:
    def __init__(self, token_file, calendar_id, *, attempts=4, max_pages=1000,
                 max_events=100000, sleep=time.sleep, jitter=random.random):
        if not isinstance(calendar_id, str) or not calendar_id.strip():
            raise ValueError("An explicit Google calendar ID is required")
        if not 1 <= attempts <= 8 or max_pages < 1 or max_events < 1:
            raise ValueError("Invalid Google read limits")
        self.tokens = GoogleTokens(token_file)
        self.calendar_id = calendar_id
        self.attempts = attempts
        self.max_pages = max_pages
        self.max_events = max_events
        self.sleep = sleep
        self.jitter = jitter

    def _retry_after(self, value):
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return 0
        if not 0 <= delay <= 60:
            raise GoogleError("google_retry_deferred")
        return delay

    def _page(self, session, params):
        url = "https://www.googleapis.com/calendar/v3/calendars/" + quote(self.calendar_id, safe="") + "/events"
        for attempt in range(self.attempts):
            token = self.tokens.access_token()
            retry_after = 0
            try:
                with quiet_network(), session.get(
                    url, params=params, headers={"Authorization": "Bearer " + token},
                    timeout=(5, 30), allow_redirects=False,
                ) as response:
                    if response.status_code == 200:
                        payload = response.json()
                        if not isinstance(payload, dict):
                            raise GoogleError("google_invalid_page")
                        return payload
                    retryable = response.status_code in {429, 500, 502, 503, 504}
                    if response.status_code == 403:
                        try:
                            reasons = response.json().get("error", {}).get("errors", [])
                            retryable = any(item.get("reason") in {"rateLimitExceeded", "userRateLimitExceeded"}
                                            for item in reasons)
                        except (ValueError, TypeError, AttributeError):
                            retryable = False
                    if not retryable:
                        raise GoogleError("google_calendar_access_failed")
                    retry_after = self._retry_after(response.headers.get("Retry-After", "0"))
            except requests.exceptions.SSLError:
                raise GoogleError("google_tls_failed") from None
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                pass
            except GoogleError:
                raise
            except Exception:
                raise GoogleError("google_invalid_response") from None
            if attempt + 1 == self.attempts:
                raise GoogleError("google_calendar_retry_exhausted")
            self.sleep(max(retry_after, min(60, 2 ** attempt + self.jitter())))

    def list_events(self):
        events = []
        event_ids = set()
        page_tokens = set()
        params = {"singleEvents": "false", "showDeleted": "true", "maxResults": 2500}
        try:
            with requests.Session() as session:
                session.trust_env = False
                for unused_page in range(self.max_pages):
                    payload = self._page(session, params)
                    items = payload.get("items", [])
                    if not isinstance(items, list):
                        raise GoogleError("google_invalid_page")
                    for event in items:
                        if (not isinstance(event, dict) or not isinstance(event.get("id"), str)
                                or not event["id"] or event["id"] in event_ids):
                            raise GoogleError("google_invalid_or_duplicate_event")
                        event_ids.add(event["id"])
                        events.append(event)
                        if len(events) > self.max_events:
                            raise GoogleError("google_event_limit")
                    next_token = payload.get("nextPageToken")
                    if next_token is None:
                        return events
                    if not isinstance(next_token, str) or not next_token or next_token in page_tokens:
                        raise GoogleError("google_invalid_pagination")
                    page_tokens.add(next_token)
                    params = {**params, "pageToken": next_token}
                raise GoogleError("google_page_limit")
        except GoogleError:
            raise
        except Exception:
            raise GoogleError("google_calendar_read_failed") from None
