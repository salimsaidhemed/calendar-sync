from contextlib import contextmanager
import io
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape

import requests

from calendar_sync.caldav import CalDAVCalendar, CalDAVError


BASE = 'https://nextcloud.tcadc.com/remote.php/dav/calendars/test/uat/'
ICS = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:test-uid\r\nDTSTART:20261107T080000Z\r\nDTEND:20261107T090000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'


def member(href='one.ics', data=ICS, etag='"etag"', status='HTTP/1.1 200 OK'):
    return '<d:response><d:href>' + escape(href) + '</d:href><d:propstat><d:prop><d:getetag>' + escape(etag) + '</d:getetag><c:calendar-data>' + escape(data) + '</c:calendar-data></d:prop><d:status>' + status + '</d:status></d:propstat></d:response>'


def multistatus(*members):
    return ('<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">' + ''.join(members) + '</d:multistatus>').encode()


@contextmanager
def response(body, status=207):
    value = Mock(status_code=status)
    value.iter_content.return_value = [body]
    yield value


class CalDAVTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.secret = Path(self.directory.name) / 'password'
        self.secret.write_text('SENTINEL_PASSWORD\n')
        self.secret.chmod(0o600)
        self.calendar = CalDAVCalendar(BASE, 'test', self.secret)
        patcher = patch('calendar_sync.caldav.requests.Session')
        self.session = patcher.start().return_value.__enter__.return_value
        self.addCleanup(patcher.stop)

    def test_http_loopback_is_rejected_without_explicit_opt_in(self):
        for host in ('localhost', '127.0.0.1', '[::1]'):
            origin = 'http://' + host + ':8090'
            with self.assertRaisesRegex(CalDAVError, '^caldav_https_required$'):
                CalDAVCalendar(origin + '/uat/', 'test', self.secret, origin_url=origin)
        self.session.request.assert_not_called()

    def test_explicit_http_loopback_reads_preserve_scheme_and_exact_origin(self):
        for host in ('localhost', '127.0.0.1', '[::1]'):
            origin = 'http://' + host + ':8090'
            calendar = CalDAVCalendar(origin + '/uat/', 'test', self.secret,
                                      origin_url=origin, allow_http_loopback=True)
            self.session.request.side_effect = [response(multistatus(member(origin + '/uat/one.ics')))]
            resource = calendar.list_resources()[0]
            self.assertEqual(resource.href, origin + '/uat/one.ics')
            self.assertEqual(self.session.request.call_args.args, ('REPORT', origin + '/uat/'))
            self.assertFalse(self.session.request.call_args.kwargs['allow_redirects'])

    def test_http_opt_in_rejects_non_loopback_hosts_before_network(self):
        for host in ('nextcloud.tcadc.com', '192.168.1.10', '10.0.0.2', '0.0.0.0',
                     'localhost.evil.invalid', '127.1', '2130706433', '[::ffff:127.0.0.1]'):
            origin = 'http://' + host + ':8090'
            with self.subTest(host=host), self.assertRaisesRegex(CalDAVError, '^caldav_http_loopback_only$'):
                CalDAVCalendar(origin + '/uat/', 'test', self.secret,
                               origin_url=origin, allow_http_loopback=True)
        self.session.request.assert_not_called()

    def test_http_opt_in_requires_boolean_and_matching_explicit_origin(self):
        for value in ('true', 1, None):
            with self.assertRaisesRegex(CalDAVError, '^caldav_invalid_configuration$'):
                CalDAVCalendar(BASE, 'test', self.secret, allow_http_loopback=value)
        with self.assertRaisesRegex(CalDAVError, '^caldav_calendar_outside_origin$'):
            CalDAVCalendar('http://localhost:8090/uat/', 'test', self.secret, allow_http_loopback=True)
        with self.assertRaisesRegex(CalDAVError, '^caldav_calendar_outside_origin$'):
            CalDAVCalendar('http://localhost:8091/uat/', 'test', self.secret,
                           origin_url='http://localhost:8090', allow_http_loopback=True)
        self.session.request.assert_not_called()

    def test_http_loopback_still_rejects_resource_escapes_and_redirects(self):
        origin = 'http://localhost:8090'
        calendar = CalDAVCalendar(origin + '/uat/', 'test', self.secret,
                                  origin_url=origin, allow_http_loopback=True)
        for href in ('http://localhost:8091/uat/one.ics', 'http://127.0.0.1:8090/uat/one.ics',
                     'http://localhost:8090/other/one.ics', '../one.ics', 'http://evil.invalid/uat/one.ics'):
            self.session.request.side_effect = [response(multistatus(member(href)))]
            with self.assertRaises(CalDAVError):
                calendar.list_resources()
        self.session.request.side_effect = [response(b'', 302)]
        with self.assertRaisesRegex(CalDAVError, '^caldav_redirect_rejected$'):
            calendar.list_resources()

    def test_report_uses_explicit_calendar_verified_tls_and_no_writes(self):
        self.session.request.side_effect = [response(multistatus(member()))]
        result = self.calendar.list_resources()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].uid, 'test-uid')
        self.assertEqual(result[0].etag, '"etag"')
        self.assertEqual(result[0].href, BASE + 'one.ics')
        self.assertIsNone(result[0].skip_reason)
        arguments = self.session.request.call_args
        self.assertEqual(arguments.args, ('REPORT', BASE))
        self.assertTrue(arguments.kwargs['verify'])
        self.assertFalse(arguments.kwargs['allow_redirects'])
        self.assertEqual(arguments.kwargs['auth'], ('test', 'SENTINEL_PASSWORD'))
        self.assertEqual(arguments.kwargs['timeout'], (5, 30))
        self.assertFalse(self.session.trust_env)
        self.assertEqual(self.session.request.call_count, 1)

    def test_constructor_rejects_insecure_foreign_and_ambiguous_urls_before_network(self):
        for url in ('http://nextcloud.tcadc.com/cal/', 'https://evil.invalid/cal/',
                    BASE + '?token=x', BASE + '#fragment', BASE.replace('/test/', '/test/../'),
                    BASE.replace('/test/', '/test/%252e%252e/'), BASE.replace('/test/', '/test/%2f/'),
                    'https://user:secret@nextcloud.tcadc.com/cal/', BASE.rstrip('/')):
            with self.subTest(url=url), self.assertRaises(CalDAVError):
                CalDAVCalendar(url, 'test', self.secret)
        self.session.request.assert_not_called()

    def test_href_scope_rejects_other_origins_siblings_nested_and_traversal(self):
        for href in ('https://evil.invalid/event.ics', '/other/event.ics', '../event.ics',
                     '%2e%2e/event.ics', 'nested/event.ics', 'event%2fother.ics',
                     'one.ics?token=secret', '//evil.invalid/event.ics'):
            self.session.request.side_effect = [response(multistatus(member(href)))]
            with self.subTest(href=href), self.assertRaises(CalDAVError):
                self.calendar.list_resources()

    def test_same_origin_absolute_and_encoded_hrefs_are_canonicalized(self):
        self.session.request.side_effect = [response(multistatus(member(BASE + 'space%20event.ics')))]
        self.assertEqual(self.calendar.list_resources()[0].href, BASE + 'space%20event.ics')

    def test_bad_secret_permissions_missing_symlink_and_empty_password_fail_before_network(self):
        self.secret.chmod(0o644)
        with self.assertRaisesRegex(CalDAVError, 'secret_permissions'):
            self.calendar.list_resources()
        self.secret.chmod(0o600)
        alias = self.secret.with_name('alias')
        alias.symlink_to(self.secret)
        with self.assertRaises(CalDAVError):
            CalDAVCalendar(BASE, 'test', alias).list_resources()
        self.secret.write_text('')
        with self.assertRaisesRegex(CalDAVError, 'secret_format'):
            self.calendar.list_resources()
        with self.assertRaisesRegex(CalDAVError, 'secret_missing'):
            CalDAVCalendar(BASE, 'test', self.secret.with_name('missing')).list_resources()
        self.session.request.assert_not_called()

    def test_redirect_auth_permission_not_found_and_partial_status_codes_are_safe(self):
        cases = {302: 'caldav_redirect_rejected', 401: 'caldav_authentication_failed',
                 403: 'caldav_permission_denied', 404: 'caldav_calendar_not_found',
                 507: 'caldav_partial_response', 500: 'caldav_read_failed'}
        for status, expected in cases.items():
            self.session.request.side_effect = [response(b'SENTINEL', status)]
            with self.assertRaises(CalDAVError) as caught:
                self.calendar.list_resources()
            self.assertEqual(str(caught.exception), expected)

    def test_tls_and_interrupted_transport_fail_without_partial_results(self):
        self.session.request.side_effect = requests.exceptions.SSLError('SENTINEL')
        with self.assertRaisesRegex(CalDAVError, 'caldav_tls_failed'):
            self.calendar.list_resources()

        @contextmanager
        def interrupted():
            value = Mock(status_code=207)
            def chunks(**kwargs):
                yield multistatus(member())
                raise requests.exceptions.ConnectionError('SENTINEL')
            value.iter_content.side_effect = chunks
            yield value

        self.session.request.side_effect = [interrupted()]
        with self.assertRaisesRegex(CalDAVError, 'caldav_response_interrupted'):
            self.calendar.list_resources()

    def test_connection_timeout_and_stream_errors_have_safe_distinct_codes(self):
        for exception, expected in (
            (requests.exceptions.ConnectionError('SENTINEL_ENDPOINT'), 'caldav_connection_failed'),
            (requests.exceptions.ConnectTimeout('SENTINEL'), 'caldav_timeout'),
            (requests.exceptions.ReadTimeout('SENTINEL'), 'caldav_timeout'),
            (requests.exceptions.ChunkedEncodingError('SENTINEL'), 'caldav_response_interrupted'),
            (requests.exceptions.ContentDecodingError('SENTINEL'), 'caldav_response_interrupted'),
        ):
            self.session.request.side_effect = exception
            with self.assertRaises(CalDAVError) as caught:
                self.calendar.list_resources()
            self.assertEqual(str(caught.exception), expected)
            self.assertNotIn('SENTINEL', str(caught.exception))

    def test_xml_syntax_and_forbidden_entities_have_safe_distinct_codes(self):
        for body, expected in (
            (b'<broken SENTINEL', 'caldav_xml_invalid'),
            (b'<!DOCTYPE d:multistatus [<!ENTITY secret SYSTEM "file:///SENTINEL">]><d:multistatus xmlns:d="DAV:">&secret;</d:multistatus>', 'caldav_xml_rejected'),
        ):
            self.session.request.side_effect = [response(body)]
            with self.assertRaises(CalDAVError) as caught:
                self.calendar.list_resources()
            self.assertEqual(str(caught.exception), expected)

    def test_icalendar_parser_exceptions_are_sanitized_as_calendar_data_errors(self):
        self.session.request.side_effect = [response(multistatus(member()))]
        with patch('calendar_sync.caldav.Calendar.from_ical', side_effect=ValueError('SENTINEL_EVENT')):
            with self.assertRaisesRegex(CalDAVError, '^caldav_invalid_calendar_data$'):
                self.calendar.list_resources()

    def test_dtd_entities_malformed_xml_and_unexpected_roots_are_rejected(self):
        for body in (b'<!DOCTYPE d:multistatus [<!ENTITY x SYSTEM "file:///etc/passwd">]><d:multistatus xmlns:d="DAV:">&x;</d:multistatus>',
                     b'<broken', b'<html/>', multistatus('<d:error/>'), multistatus('<c:limit/>')):
            self.session.request.side_effect = [response(body)]
            with self.assertRaises(CalDAVError):
                self.calendar.list_resources()

    def test_missing_properties_and_failed_propstat_reject_entire_report(self):
        for extra in (member('two.ics', status='HTTP/1.1 404 Not Found'),
                      member('two.ics', data=''), member('two.ics', etag=''),
                      '<d:response><d:href>two.ics</d:href><d:status>HTTP/1.1 507 Limit</d:status></d:response>'):
            self.session.request.side_effect = [response(multistatus(member(), extra))]
            with self.assertRaises(CalDAVError):
                self.calendar.list_resources()

    def test_duplicate_href_uid_and_weak_etag_fail_closed(self):
        for body in (multistatus(member(), member()), multistatus(member(), member('two.ics')),
                     multistatus(member(etag='W/"weak"'))):
            self.session.request.side_effect = [response(body)]
            with self.assertRaises(CalDAVError):
                self.calendar.list_resources()

    def test_response_and_resource_limits_do_not_return_truncated_success(self):
        self.calendar.max_bytes = 2
        self.session.request.side_effect = [response(multistatus(member()))]
        with self.assertRaisesRegex(CalDAVError, 'response_limit'):
            self.calendar.list_resources()
        self.calendar.max_bytes = 10000
        self.calendar.max_resources = 1
        self.session.request.side_effect = [response(multistatus(member(), member('two.ics', data=ICS.replace('test-uid', 'second'))))]
        with self.assertRaisesRegex(CalDAVError, 'resource_limit'):
            self.calendar.list_resources()

    def test_recurrence_cancelled_alarm_and_floating_resources_have_skip_reasons(self):
        for insertion, reason in (('RRULE:FREQ=DAILY\r\n', 'unsupported_recurrence'),
                                  ('RDATE:20261108T080000Z\r\n', 'unsupported_recurrence'),
                                  ('EXDATE:20261108T080000Z\r\n', 'unsupported_recurrence'),
                                  ('RECURRENCE-ID:20261107T080000Z\r\n', 'unsupported_recurrence'),
                                  ('STATUS:CANCELLED\r\n', 'cancelled'),
                                  ('BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:alarm\r\nTRIGGER:-PT5M\r\nEND:VALARM\r\n', 'unsupported_component')):
            data = ICS.replace('END:VEVENT', insertion + 'END:VEVENT')
            self.session.request.side_effect = [response(multistatus(member(data=data)))]
            self.assertEqual(self.calendar.list_resources()[0].skip_reason, reason)
        for data in (ICS.replace('080000Z', '080000').replace('090000Z', '090000'),
                     ICS.replace('DTEND:20261107T090000Z\r\n', ''),
                     ICS.replace('DTEND:20261107T090000Z', 'DTEND;VALUE=DATE:20261108'),
                     ICS.replace('090000Z', '080000Z')):
            self.session.request.side_effect = [response(multistatus(member(data=data)))]
            self.assertEqual(self.calendar.list_resources()[0].skip_reason, 'unsupported_event_time')

    def test_invalid_calendar_data_or_missing_uid_is_rejected(self):
        for data in ('SENTINEL_INVALID', ICS.replace('UID:test-uid\r\n', ''),
                     ICS.replace('UID:test-uid', 'UID:first\r\nUID:second')):
            self.session.request.side_effect = [response(multistatus(member(data=data)))]
            with self.assertRaises(CalDAVError):
                self.calendar.list_resources()

    def test_empty_calendar_is_a_read_result_not_delete_instruction(self):
        self.session.request.side_effect = [response(multistatus())]
        self.assertEqual(self.calendar.list_resources(), [])
        self.assertFalse(hasattr(self.calendar, 'delete'))

    def test_credentials_event_data_and_raw_exceptions_are_not_logged(self):
        output = io.StringIO()
        root = logging.getLogger()
        handler = logging.StreamHandler(output)
        original = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, original)
        def request(*args, **kwargs):
            logging.getLogger('urllib3').error('SENTINEL_PASSWORD SENTINEL_EVENT')
            raise RuntimeError('SENTINEL')
        self.session.request.side_effect = request
        with self.assertRaises(CalDAVError) as caught:
            self.calendar.list_resources()
        self.assertNotIn('SENTINEL', str(caught.exception))
        self.assertEqual(output.getvalue(), '')
