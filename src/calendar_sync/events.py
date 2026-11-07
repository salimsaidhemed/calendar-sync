"""Provider-neutral event values and deterministic Google normalization."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event


@dataclass(frozen=True, repr=False)
class NormalizedEvent:
    source_id: str
    summary: str
    description: str
    location: str
    start: date | datetime
    end: date | datetime

    def __post_init__(self):
        if type(self.start) is not type(self.end) or type(self.start) not in {date, datetime}:
            raise ValueError('invalid_event_boundaries')
        if isinstance(self.start, datetime) and any(
            value.tzinfo is None or value.utcoffset() is None for value in (self.start, self.end)
        ):
            raise ValueError('floating_time')
        if isinstance(self.start, datetime):
            object.__setattr__(self, 'start', self.start.astimezone(timezone.utc))
            object.__setattr__(self, 'end', self.end.astimezone(timezone.utc))
        if self.end <= self.start:
            raise ValueError('invalid_duration')
        for field in ('source_id', 'summary', 'description', 'location'):
            object.__setattr__(self, field, _text(getattr(self, field)))
        if not self.source_id or any(char.isspace() for char in self.source_id):
            raise ValueError('invalid_source_id')

    def fingerprint(self):
        value = [self.source_id, self.summary, self.description, self.location,
                 self.start.isoformat(), self.end.isoformat()]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True, repr=False)
class NormalizationResult:
    event: NormalizedEvent | None
    reason: str
    omitted_fields: tuple[str, ...] = ()


class InvalidEvent(ValueError):
    pass


def _text(value):
    if not isinstance(value, str) or any((ord(char) < 32 and char not in '\r\n\t') or ord(char) == 127 for char in value):
        raise InvalidEvent('invalid_text')
    return value.replace('\r\n', '\n').replace('\r', '\n')


def _boundary(value):
    if not isinstance(value, dict) or ('date' in value) == ('dateTime' in value):
        raise InvalidEvent('invalid_time')
    if 'date' in value:
        if not isinstance(value['date'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value['date']):
            raise InvalidEvent('invalid_time')
        return date.fromisoformat(value['date'])
    raw = value['dateTime']
    if not isinstance(raw, str) or not re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?', raw
    ):
        raise InvalidEvent('invalid_time')
    fraction = re.search(r'\.(\d+)', raw)
    if fraction and any(char != '0' for char in fraction.group(1)):
        raise InvalidEvent('unsupported_subsecond')
    instant = datetime.fromisoformat(raw)
    zone = ZoneInfo(value['timeZone']) if 'timeZone' in value else None
    if instant.tzinfo is None:
        if zone is None:
            raise InvalidEvent('floating_time')
        possibilities = set()
        for fold in (0, 1):
            candidate = instant.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
            if candidate.astimezone(zone).replace(tzinfo=None) == instant:
                possibilities.add(candidate)
        if len(possibilities) != 1:
            raise InvalidEvent('ambiguous_or_nonexistent_time')
        return possibilities.pop()
    if zone is not None and instant.utcoffset() != instant.astimezone(zone).utcoffset():
        raise InvalidEvent('timezone_offset_mismatch')
    return instant.astimezone(timezone.utc)


def normalize_google_event(value):
    if not isinstance(value, dict):
        return NormalizationResult(None, 'invalid_event')
    if value.get('status') == 'cancelled':
        return NormalizationResult(None, 'cancelled')
    if any(field in value for field in ('recurrence', 'recurringEventId', 'originalStartTime')):
        return NormalizationResult(None, 'unsupported_recurrence')
    if value.get('eventType', 'default') != 'default':
        return NormalizationResult(None, 'unsupported_event_type')
    omitted = tuple(sorted(field for field in (
        'attendees', 'organizer', 'creator', 'conferenceData', 'hangoutLink',
        'attachments', 'reminders', 'visibility', 'transparency', 'colorId',
    ) if field in value))
    try:
        source_id = _text(value.get('id'))
        if not source_id or any(char.isspace() for char in source_id):
            raise InvalidEvent('invalid_source_id')
        start, end = _boundary(value.get('start')), _boundary(value.get('end'))
        if type(start) is not type(end) or end <= start:
            raise InvalidEvent('invalid_duration')
        event = NormalizedEvent(source_id, _text(value.get('summary', '')),
                                _text(value.get('description', '')),
                                _text(value.get('location', '')), start, end)
        return NormalizationResult(event, 'supported_with_omissions' if omitted else 'supported', omitted)
    except InvalidEvent as error:
        return NormalizationResult(None, str(error), omitted)
    except (ValueError, TypeError, KeyError, OverflowError):
        return NormalizationResult(None, 'invalid_time', omitted)


def serialize_event(event, uid, *, stamp):
    if not isinstance(uid, str) or not uid or len(uid) > 255 or any(ord(char) < 33 or ord(char) == 127 for char in uid):
        raise ValueError('invalid_uid')
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('aware_stamp_required')
    if not isinstance(event, NormalizedEvent):
        raise ValueError('normalized_event_required')
    calendar = Calendar()
    calendar.add('prodid', '-//TCADC//Calendar Sync//EN')
    calendar.add('version', '2.0')
    component = Event()
    for name, value in (
        ('uid', uid), ('dtstamp', stamp.astimezone(timezone.utc).replace(microsecond=0)),
        ('dtstart', event.start.astimezone(timezone.utc) if isinstance(event.start, datetime) else event.start),
        ('dtend', event.end.astimezone(timezone.utc) if isinstance(event.end, datetime) else event.end),
        ('summary', event.summary),
        ('description', event.description), ('location', event.location),
    ):
        component.add(name, value)
    calendar.add_component(component)
    return calendar.to_ical()
