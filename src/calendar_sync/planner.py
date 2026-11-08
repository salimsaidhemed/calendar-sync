"""Pure, fail-closed planning; no provider write or applied-state mutation APIs."""

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import re
from urllib.parse import urlsplit

from icalendar import Calendar

from calendar_sync.caldav import CalDAVError, CalendarResource, _url
from calendar_sync.events import NormalizedEvent, normalize_google_event, serialize_event


OWNER = 'X-TCADC-SYNC-OWNER'
SOURCE = 'X-TCADC-SYNC-SOURCE'
BASELINE = 'X-TCADC-SYNC-FINGERPRINT'
ACTIONS = ('create', 'update', 'unchanged', 'conflict', 'unsupported', 'missing')


class PlanningError(RuntimeError):
    pass


def _hash(values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _identifier(value):
    return isinstance(value, str) and bool(value) and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)


def _etag(value):
    return isinstance(value, str) and re.fullmatch(r'"[^"\x00-\x20\x7f]*"', value) is not None


@dataclass(frozen=True, repr=False)
class PlanContext:
    configuration_id: str
    google_calendar_id: str
    destination_calendar_url: str

    def __post_init__(self):
        try:
            if (not _identifier(self.configuration_id) or not _identifier(self.google_calendar_id)
                    or self.google_calendar_id == 'primary'):
                raise ValueError()
            canonical = _url(self.destination_calendar_url, allow_http_loopback=True)
            parsed = urlsplit(canonical)
            if not parsed.netloc or not parsed.path.endswith('/') or parsed.path == '/':
                raise ValueError()
            object.__setattr__(self, 'destination_calendar_url', canonical)
        except Exception:
            raise PlanningError('planner_invalid_context') from None

    def owner_marker(self):
        return _hash(['tcadc-plan-v1', self.configuration_id, self.google_calendar_id,
                      self.destination_calendar_url])


@dataclass(frozen=True, repr=False)
class EventIdentity:
    href: str
    uid: str
    source_marker: str


def event_identity(context, source_id):
    if not _identifier(source_id):
        raise PlanningError('planner_invalid_source_id')
    digest = _hash([context.owner_marker(), source_id])
    return EventIdentity(context.destination_calendar_url + 'tcadc-' + digest + '.ics',
                         'tcadc-' + digest + '@calendar-sync', digest)


@dataclass(frozen=True, repr=False)
class MappingSnapshot:
    configuration_id: str
    source_event_id: str
    destination_href: str
    destination_uid: str
    source_fingerprint: str
    destination_etag: str


@dataclass(frozen=True, repr=False)
class PlanItem:
    action: str
    reason: str
    source_id: str | None
    destination_href: str | None = None
    destination_uid: str | None = None
    source_fingerprint: str | None = None
    expected_etag: str | None = None
    event: NormalizedEvent | None = None
    omitted_fields: tuple[str, ...] = ()

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise PlanningError('planner_invalid_action')


@dataclass(frozen=True, repr=False)
class SyncPlan:
    context: PlanContext
    items: tuple[PlanItem, ...]
    dry_run: bool = True

    def __post_init__(self):
        if self.dry_run is not True:
            raise PlanningError('planner_is_dry_run_only')
        try:
            items = tuple(self.items)
        except Exception:
            raise PlanningError('planner_invalid_plan') from None
        if not isinstance(self.context, PlanContext) or any(not isinstance(item, PlanItem) for item in items):
            raise PlanningError('planner_invalid_plan')
        object.__setattr__(self, 'items', items)

    def counts(self):
        counts = Counter(item.action for item in self.items)
        return {action: counts[action] for action in ACTIONS}


def serialize_owned_event(context, event, *, stamp):
    identity = event_identity(context, event.source_id)
    calendar = Calendar.from_ical(serialize_event(event, identity.uid, stamp=stamp))
    component = calendar.walk('VEVENT')[0]
    for name, value in ((OWNER, context.owner_marker()), (SOURCE, identity.source_marker),
                        (BASELINE, event.fingerprint())):
        component.add(name, value)
    return calendar.to_ical()


def _owned_baseline(context, identity, mapping, resource):
    try:
        calendar = Calendar.from_ical(resource.data)
        components = calendar.walk('VEVENT')
        if (calendar.name != 'VCALENDAR' or len(components) != 1
                or str(calendar.get('VERSION', '')) != '2.0'
                or set(calendar.keys()) - {'VERSION', 'PRODID', 'CALSCALE'}
                or any(component.errors for component in calendar.walk())
                or any(component.name not in {'VCALENDAR', 'VEVENT', 'VTIMEZONE', 'STANDARD', 'DAYLIGHT'}
                       for component in calendar.walk())):
            return False
        event = components[0]
        allowed = {'UID', 'DTSTAMP', 'DTSTART', 'DTEND', 'SUMMARY', 'DESCRIPTION', 'LOCATION',
                   'CREATED', 'LAST-MODIFIED', 'SEQUENCE', OWNER, SOURCE, BASELINE}
        if set(event.keys()) - allowed or any(isinstance(value, list) for value in event.values()):
            return False
        if (str(event.get('UID', '')) != identity.uid
                or str(event.get(OWNER, '')) != context.owner_marker()
                or str(event.get(SOURCE, '')) != identity.source_marker
                or str(event.get(BASELINE, '')) != mapping.source_fingerprint):
            return False
        baseline = NormalizedEvent(mapping.source_event_id, str(event.get('SUMMARY', '')),
                                   str(event.get('DESCRIPTION', '')), str(event.get('LOCATION', '')),
                                   event.decoded('DTSTART'), event.decoded('DTEND'))
        return baseline.fingerprint() == mapping.source_fingerprint
    except Exception:
        return False


def build_plan(context, source_events, destination_resources, mappings=(), *,
               source_complete=False, destination_complete=False):
    if source_complete is not True or destination_complete is not True:
        raise PlanningError('planner_incomplete_snapshot')
    if not isinstance(context, PlanContext):
        raise PlanningError('planner_invalid_context')
    by_href, by_uid, by_source = {}, {}, {}
    try:
        for resource in destination_resources:
            if not isinstance(resource, CalendarResource) or not _etag(resource.etag) or not _identifier(resource.uid):
                raise PlanningError('planner_invalid_destination')
            href = _url(resource.href, allow_http_loopback=True)
            remainder = href.removeprefix(context.destination_calendar_url)
            if (href != resource.href or not href.startswith(context.destination_calendar_url)
                    or not remainder or '/' in remainder):
                raise PlanningError('planner_destination_outside_calendar')
            if href in by_href or resource.uid in by_uid:
                raise PlanningError('planner_duplicate_destination')
            by_href[href], by_uid[resource.uid] = resource, resource
        mapping_hrefs, mapping_uids = set(), set()
        for mapping in mappings:
            if not isinstance(mapping, MappingSnapshot) or mapping.configuration_id != context.configuration_id:
                raise PlanningError('planner_mapping_scope_mismatch')
            if (not _identifier(mapping.source_event_id) or not _etag(mapping.destination_etag)
                    or not _identifier(mapping.destination_uid)
                    or not isinstance(mapping.source_fingerprint, str)
                    or not re.fullmatch(r'[0-9a-f]{64}', mapping.source_fingerprint)):
                raise PlanningError('planner_invalid_mapping')
            href = _url(mapping.destination_href, allow_http_loopback=True)
            remainder = href.removeprefix(context.destination_calendar_url)
            if (href != mapping.destination_href or not href.startswith(context.destination_calendar_url)
                    or not remainder or '/' in remainder):
                raise PlanningError('planner_mapping_outside_calendar')
            if (mapping.source_event_id in by_source or mapping.destination_href in mapping_hrefs
                    or mapping.destination_uid in mapping_uids):
                raise PlanningError('planner_duplicate_mapping')
            by_source[mapping.source_event_id] = mapping
            mapping_hrefs.add(mapping.destination_href)
            mapping_uids.add(mapping.destination_uid)
        sources = tuple(source_events)
    except PlanningError:
        raise
    except Exception:
        raise PlanningError('planner_invalid_snapshot') from None
    seen, items = set(), []
    for source in sources:
        source_id = source.get('id') if isinstance(source, dict) else None
        source_id = source_id if _identifier(source_id) else None
        if source_id is not None:
            if source_id in seen:
                raise PlanningError('planner_duplicate_source')
            seen.add(source_id)
        result = normalize_google_event(source)
        mapping = by_source.get(source_id)
        if result.event is None:
            action = 'missing' if result.reason == 'cancelled' and mapping else 'unsupported'
            target = dict(destination_href=mapping.destination_href, destination_uid=mapping.destination_uid,
                          expected_etag=mapping.destination_etag) if mapping else {}
            items.append(PlanItem(action, result.reason, source_id, omitted_fields=result.omitted_fields, **target))
            continue
        event = result.event
        identity = event_identity(context, event.source_id)
        resource = by_href.get(identity.href)
        base = dict(source_id=event.source_id, destination_href=identity.href, destination_uid=identity.uid,
                    source_fingerprint=event.fingerprint(), event=event, omitted_fields=result.omitted_fields,
                    expected_etag=mapping.destination_etag if mapping else None)
        if mapping is None:
            if resource is not None or identity.uid in by_uid:
                items.append(PlanItem('conflict', 'mapping_missing_or_identity_collision', **base))
            elif identity.href in mapping_hrefs or identity.uid in mapping_uids:
                items.append(PlanItem('conflict', 'mapping_identity_collision', **base))
            else:
                items.append(PlanItem('create', 'new_source', **base))
        elif mapping.destination_href != identity.href or mapping.destination_uid != identity.uid:
            items.append(PlanItem('conflict', 'mapping_identity_mismatch', **base))
        elif resource is None:
            items.append(PlanItem('conflict', 'destination_missing', **base))
        elif resource.uid != identity.uid:
            items.append(PlanItem('conflict', 'destination_identity_mismatch', **base))
        elif resource.skip_reason is not None:
            items.append(PlanItem('conflict', 'destination_unsupported', **base))
        elif resource.etag != mapping.destination_etag:
            items.append(PlanItem('conflict', 'destination_etag_changed', **base))
        elif not _owned_baseline(context, identity, mapping, resource):
            items.append(PlanItem('conflict', 'ownership_or_baseline_mismatch', **base))
        elif event.fingerprint() == mapping.source_fingerprint:
            items.append(PlanItem('unchanged', 'source_unchanged', **base))
        else:
            items.append(PlanItem('update', 'source_changed', **base))
    for source_id, mapping in by_source.items():
        if source_id not in seen:
            items.append(PlanItem('missing', 'source_absent', source_id,
                                  mapping.destination_href, mapping.destination_uid,
                                  expected_etag=mapping.destination_etag))
    items.sort(key=lambda item: (item.source_id or '', item.action, item.reason))
    return SyncPlan(context, tuple(items))


def preview_sync(store, configuration_id, google, caldav):
    from calendar_sync.google_oauth import GoogleError

    configuration, rows = store.planning_snapshot(configuration_id)
    context = PlanContext(configuration['id'], configuration['google_calendar_id'],
                          configuration['nextcloud_calendar_url'])
    if (google.calendar_id != context.google_calendar_id
            or caldav.calendar_url != context.destination_calendar_url):
        raise PlanningError('planner_provider_scope_mismatch')
    try:
        sources = google.list_events()
        resources = caldav.list_resources()
    except (GoogleError, CalDAVError):
        raise
    except Exception:
        raise PlanningError('planner_provider_read_failed') from None
    return build_plan(context, sources, resources, tuple(MappingSnapshot(**row) for row in rows),
                      source_complete=True, destination_complete=True)
