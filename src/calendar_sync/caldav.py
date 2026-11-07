"""Explicit-calendar, read-only CalDAV adapter with strict resource boundaries."""

from dataclasses import dataclass
from datetime import date, datetime
import os
import re
import stat
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from defusedxml import ElementTree
from icalendar import Calendar
import requests

from calendar_sync.security import quiet_network


DAV = '{DAV:}'
CAL = '{urn:ietf:params:xml:ns:caldav}'
QUERY = b'''<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
<d:prop><d:getetag/><c:calendar-data/></d:prop>
<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"/>
</c:comp-filter></c:filter></c:calendar-query>'''


class CalDAVError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class CalendarResource:
    href: str
    etag: str
    uid: str
    data: bytes
    skip_reason: str | None


def _url(value):
    if (not isinstance(value, str) or not value or '?' in value or '#' in value
            or any(ord(char) <= 32 or ord(char) >= 127 for char in value)):
        raise CalDAVError('caldav_invalid_url')
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        raise CalDAVError('caldav_invalid_url')
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != 'https' or not parsed.hostname or not parsed.netloc:
            raise CalDAVError('caldav_https_required')
        port = parsed.port or 443
        host = parsed.hostname.lower()
        authority = ('[' + host + ']') if ':' in host else host
        if port != 443:
            authority += ':' + str(port)
    else:
        authority = ''
    segments = []
    for segment in parsed.path.split('/'):
        decoded = unquote(segment, errors='strict')
        if ('%' in decoded or decoded in {'.', '..'} or any(char in decoded for char in '/\\?#')
                or any(ord(char) < 32 or ord(char) == 127 for char in decoded)):
            raise CalDAVError('caldav_unsafe_path')
        segments.append(quote(decoded, safe="!$&'()+,;=@-._~"))
    return urlunsplit(('https' if authority else '', authority, '/'.join(segments), '', ''))


def _password(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor) as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077
                or metadata.st_uid != os.geteuid()):
            raise CalDAVError('caldav_secret_permissions')
        value = stream.read(16385).rstrip('\r\n')
        if not value or len(value) > 16384 or any(ord(char) < 32 for char in value):
            raise CalDAVError('caldav_secret_format')
        return value


def _status(value):
    match = re.fullmatch(r'HTTP/\d(?:\.\d)? ([1-5]\d\d)(?: .*)?', value or '')
    if match is None or match.group(1) != '200':
        raise CalDAVError('caldav_partial_response')


def _resource(href, etag, text):
    if not etag or not re.fullmatch(r'"[^"\x00-\x20\x7f]*"', etag):
        raise CalDAVError('caldav_invalid_etag')
    calendar = Calendar.from_ical(text)
    if calendar.name != 'VCALENDAR' or any(component.errors for component in calendar.walk()):
        raise CalDAVError('caldav_invalid_calendar_data')
    events = calendar.walk('VEVENT')
    if not events:
        raise CalDAVError('caldav_invalid_calendar_data')
    uids = [event.get('UID') for event in events]
    if any(not isinstance(uid, str) or not uid for uid in uids) or len(set(uids)) != 1:
        raise CalDAVError('caldav_invalid_calendar_data')
    reason = None
    if len(events) != 1 or any(field in event for event in events
                               for field in ('RRULE', 'RDATE', 'EXDATE', 'RECURRENCE-ID')):
        reason = 'unsupported_recurrence'
    elif str(events[0].get('STATUS', '')).upper() == 'CANCELLED':
        reason = 'cancelled'
    elif any(component.name not in {'VCALENDAR', 'VEVENT', 'VTIMEZONE', 'STANDARD', 'DAYLIGHT'}
             for component in calendar.walk()):
        reason = 'unsupported_component'
    else:
        event = events[0]
        if any(field not in event or isinstance(event[field], list) for field in ('DTSTART', 'DTEND')):
            reason = 'unsupported_event_time'
        else:
            start, end = event.decoded('DTSTART'), event.decoded('DTEND')
            if (type(start) is not type(end) or type(start) not in {date, datetime}
                    or (isinstance(start, datetime) and any(value.tzinfo is None or value.utcoffset() is None
                                                           for value in (start, end)))
                    or end <= start):
                reason = 'unsupported_event_time'
    return CalendarResource(href, etag, str(uids[0]), text.encode('utf-8'), reason)


class CalDAVCalendar:
    def __init__(self, calendar_url, username, password_file, *,
                 origin_url='https://nextcloud.tcadc.com', max_bytes=8388608, max_resources=10000):
        try:
            origin = _url(origin_url)
            parsed = urlsplit(origin)
            if not parsed.netloc or parsed.path not in {'', '/'}:
                raise CalDAVError('caldav_invalid_origin')
            self.origin = urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
            self.calendar_url = _url(calendar_url)
            target = urlsplit(self.calendar_url)
            if (not target.netloc or not target.path.endswith('/') or target.path == '/'
                    or self.origin != urlunsplit((target.scheme, target.netloc, '', '', ''))):
                raise CalDAVError('caldav_calendar_outside_origin')
            if not isinstance(username, str) or not username or ':' in username or any(ord(char) < 32 for char in username):
                raise CalDAVError('caldav_invalid_username')
            if max_bytes < 1 or max_resources < 1:
                raise CalDAVError('caldav_invalid_limits')
            self.username = username
            self.password_file = password_file
            self.max_bytes = max_bytes
            self.max_resources = max_resources
        except CalDAVError:
            raise
        except Exception:
            raise CalDAVError('caldav_invalid_configuration') from None

    def _href(self, value):
        safe = _url(value)
        resolved = _url(urljoin(self.calendar_url, safe))
        if not resolved.startswith(self.calendar_url):
            raise CalDAVError('caldav_resource_outside_calendar')
        remainder = resolved[len(self.calendar_url):]
        if not remainder or '/' in remainder:
            raise CalDAVError('caldav_resource_outside_calendar')
        return resolved

    def _parse(self, body):
        root = ElementTree.fromstring(body, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        if root.tag != DAV + 'multistatus' or root.find('.//' + DAV + 'error') is not None:
            raise CalDAVError('caldav_invalid_response')
        if any(child.tag not in {DAV + 'response', DAV + 'responsedescription'} for child in root):
            raise CalDAVError('caldav_invalid_response')
        resources, hrefs, uids = [], set(), set()
        for response in root.findall(DAV + 'response'):
            references = response.findall(DAV + 'href')
            if len(references) != 1:
                raise CalDAVError('caldav_invalid_response')
            href = self._href(references[0].text)
            if response.find(DAV + 'status') is not None:
                _status(response.findtext(DAV + 'status'))
            props = {}
            for propstat in response.findall(DAV + 'propstat'):
                _status(propstat.findtext(DAV + 'status'))
                prop = propstat.find(DAV + 'prop')
                if prop is None:
                    raise CalDAVError('caldav_invalid_response')
                for item in prop:
                    if item.tag in props:
                        raise CalDAVError('caldav_invalid_response')
                    props[item.tag] = item.text
            etag, data = props.get(DAV + 'getetag'), props.get(CAL + 'calendar-data')
            if not data:
                raise CalDAVError('caldav_partial_response')
            resource = _resource(href, etag, data)
            if href in hrefs or resource.uid in uids:
                raise CalDAVError('caldav_duplicate_resource')
            hrefs.add(href)
            uids.add(resource.uid)
            resources.append(resource)
            if len(resources) > self.max_resources:
                raise CalDAVError('caldav_resource_limit')
        return resources

    def list_resources(self):
        try:
            with quiet_network():
                password = _password(self.password_file)
                with requests.Session() as session:
                    session.trust_env = False
                    with session.request(
                        'REPORT', self.calendar_url, data=QUERY,
                        auth=(self.username, password), timeout=(5, 30),
                        headers={'Depth': '1', 'Content-Type': 'application/xml; charset=utf-8'},
                        allow_redirects=False, verify=True, stream=True,
                    ) as response:
                        if response.status_code != 207:
                            code = {401: 'caldav_authentication_failed', 403: 'caldav_permission_denied',
                                    404: 'caldav_calendar_not_found', 507: 'caldav_partial_response'}.get(
                                        response.status_code, 'caldav_read_failed')
                            if 300 <= response.status_code < 400:
                                code = 'caldav_redirect_rejected'
                            raise CalDAVError(code)
                        body = bytearray()
                        for chunk in response.iter_content(chunk_size=65536):
                            body.extend(chunk)
                            if len(body) > self.max_bytes:
                                raise CalDAVError('caldav_response_limit')
                        return self._parse(bytes(body))
        except CalDAVError:
            raise
        except requests.exceptions.SSLError:
            raise CalDAVError('caldav_tls_failed') from None
        except FileNotFoundError:
            raise CalDAVError('caldav_secret_missing') from None
        except Exception:
            raise CalDAVError('caldav_invalid_or_incomplete_read') from None
