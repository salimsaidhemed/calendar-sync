from contextlib import contextmanager
import io
import logging
import unittest
from unittest.mock import Mock, patch

import requests

from calendar_sync.google_calendar import GoogleCalendar
from calendar_sync.google_oauth import GoogleError


@contextmanager
def response(payload=None, status=200, headers=None):
    value = Mock(status_code=status, headers=headers or {})
    value.json.return_value = payload
    yield value


class CalendarReadTests(unittest.TestCase):
    def setUp(self):
        self.calendar = GoogleCalendar('/unused-token.json', 'master@example.invalid',
                                       sleep=Mock(), jitter=lambda: 0.5)
        token = patch.object(self.calendar.tokens, 'access_token', return_value='SENTINEL_ACCESS')
        token.start()
        self.addCleanup(token.stop)
        session = patch('calendar_sync.google_calendar.requests.Session')
        self.session = session.start().return_value.__enter__.return_value
        self.addCleanup(session.stop)

    def test_reads_all_pages_including_empty_pages_and_preserves_markers(self):
        recurring = {'id': 'series', 'recurrence': ['RRULE:FREQ=DAILY']}
        cancelled = {'id': 'cancelled', 'status': 'cancelled'}
        self.session.get.side_effect = [response({'items': [recurring], 'nextPageToken': 'two'}),
                                        response({'nextPageToken': 'three'}),
                                        response({'items': [cancelled]})]
        self.assertEqual(self.calendar.list_events(), [recurring, cancelled])
        calls = self.session.get.call_args_list
        self.assertIn('master%40example.invalid', calls[0].args[0])
        self.assertEqual(calls[1].kwargs['params']['pageToken'], 'two')
        self.assertEqual(calls[2].kwargs['params']['pageToken'], 'three')
        for call in calls:
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertEqual(call.kwargs['timeout'], (5, 30))
            self.assertEqual(call.kwargs['params']['singleEvents'], 'false')
            self.assertEqual(call.kwargs['params']['showDeleted'], 'true')
        self.assertFalse(self.session.trust_env)

    def test_partial_pagination_failure_never_returns_partial_events(self):
        self.session.get.side_effect = [response({'items': [{'id': 'first'}], 'nextPageToken': 'two'}),
                                        response(status=404)]
        with self.assertRaisesRegex(GoogleError, 'google_calendar_not_found'):
            self.calendar.list_events()

    def test_repeated_page_token_and_duplicate_event_fail_closed(self):
        for payloads in (
            [{'nextPageToken': 'repeat'}, {'nextPageToken': 'repeat'}],
            [{'items': [{'id': 'same'}], 'nextPageToken': 'two'}, {'items': [{'id': 'same'}]}],
        ):
            self.session.get.side_effect = [response(payload) for payload in payloads]
            with self.assertRaises(GoogleError):
                self.calendar.list_events()

    def test_rate_limit_retry_respects_retry_after(self):
        self.session.get.side_effect = [response(status=429, headers={'Retry-After': '7'}),
                                        response({'items': [{'id': 'event'}]})]
        self.assertEqual(len(self.calendar.list_events()), 1)
        self.calendar.sleep.assert_called_once_with(7)

    def test_403_rate_limits_retry_but_permission_denial_does_not(self):
        self.session.get.side_effect = [response({'error': {'errors': [{'reason': 'userRateLimitExceeded'}]}}, 403),
                                        response({})]
        self.assertEqual(self.calendar.list_events(), [])
        self.calendar.sleep.assert_called_once_with(1.5)
        self.session.get.reset_mock()
        self.session.get.side_effect = [response({'error': {'errors': [{'reason': 'forbidden'}]}}, 403)]
        with self.assertRaises(GoogleError):
            self.calendar.list_events()
        self.assertEqual(self.session.get.call_count, 1)

    def test_transient_network_errors_have_bounded_retries(self):
        self.session.get.side_effect = requests.exceptions.Timeout('SENTINEL_URL')
        with self.assertRaisesRegex(GoogleError, 'retry_exhausted') as caught:
            self.calendar.list_events()
        self.assertEqual(self.session.get.call_count, 4)
        self.assertEqual(self.calendar.sleep.call_count, 3)
        self.assertNotIn('SENTINEL', str(caught.exception))

    def test_permanent_failures_have_safe_specific_codes_without_retries(self):
        cases = {400: 'google_calendar_request_invalid', 401: 'google_calendar_token_rejected',
                 403: 'google_calendar_permission_denied', 404: 'google_calendar_not_found',
                 302: 'google_calendar_redirect_rejected', 418: 'google_calendar_access_failed'}
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.session.get.reset_mock()
                self.session.get.side_effect = [response({'error': {'message': 'SENTINEL_ACCESS'}}, status)]
                with self.assertRaises(GoogleError) as caught:
                    self.calendar.list_events()
                self.assertEqual(str(caught.exception), expected)
                self.assertEqual(self.session.get.call_count, 1)
        self.calendar.sleep.assert_not_called()

    def test_api_disabled_and_insufficient_scope_reasons_are_allowlisted(self):
        cases = (
            ('errors', 'accessNotConfigured', 'google_calendar_api_disabled'),
            ('errors', 'serviceDisabled', 'google_calendar_api_disabled'),
            ('details', 'SERVICE_DISABLED', 'google_calendar_api_disabled'),
            ('errors', 'insufficientPermissions', 'google_calendar_scope_insufficient'),
            ('details', 'ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'google_calendar_scope_insufficient'),
            ('errors', 'SENTINEL_SECRET', 'google_calendar_permission_denied'),
        )
        for field, reason, expected in cases:
            with self.subTest(reason=reason):
                self.session.get.reset_mock()
                payload = {'error': {field: [{'reason': reason, 'message': 'SENTINEL_ACCESS'}]}}
                self.session.get.side_effect = [response(payload, 403)]
                with self.assertRaises(GoogleError) as caught:
                    self.calendar.list_events()
                self.assertEqual(str(caught.exception), expected)
                self.assertEqual(self.session.get.call_count, 1)
        self.calendar.sleep.assert_not_called()

    def test_malformed_403_payload_keeps_safe_status_diagnostic(self):
        for payload in (None, [], {'error': 'SENTINEL'}, {'error': {'errors': None}},
                        {'error': {'errors': ['SENTINEL', {'reason': []}]}}):
            self.session.get.side_effect = [response(payload, 403)]
            with self.assertRaisesRegex(GoogleError, '^google_calendar_permission_denied$'):
                self.calendar.list_events()

        @contextmanager
        def invalid_json():
            value = Mock(status_code=403)
            value.json.side_effect = ValueError('SENTINEL')
            yield value

        self.session.get.side_effect = [invalid_json()]
        with self.assertRaisesRegex(GoogleError, '^google_calendar_permission_denied$'):
            self.calendar.list_events()

    def test_redirects_auth_errors_and_tls_failures_are_not_retried(self):
        for status in (301, 302, 401):
            self.session.get.reset_mock()
            self.session.get.side_effect = [response(status=status)]
            with self.assertRaises(GoogleError):
                self.calendar.list_events()
            self.assertEqual(self.session.get.call_count, 1)
        self.session.get.side_effect = requests.exceptions.SSLError('SENTINEL')
        with self.assertRaisesRegex(GoogleError, 'tls_failed'):
            self.calendar.list_events()

    def test_large_retry_after_defers_without_retrying_early(self):
        self.session.get.side_effect = [response(status=503, headers={'Retry-After': '3600'})]
        with self.assertRaisesRegex(GoogleError, 'retry_deferred'):
            self.calendar.list_events()
        self.calendar.sleep.assert_not_called()

    def test_invalid_payloads_and_limits_fail_closed(self):
        for payload in ([], {'items': {}}, {'items': [{}]}, {'nextPageToken': ''}):
            self.session.get.side_effect = [response(payload)]
            with self.assertRaises(GoogleError):
                self.calendar.list_events()
        self.calendar.max_events = 1
        self.session.get.side_effect = [response({'items': [{'id': 'one'}, {'id': 'two'}]})]
        with self.assertRaisesRegex(GoogleError, 'event_limit'):
            self.calendar.list_events()
        self.calendar.max_pages = 1
        self.session.get.side_effect = [response({'nextPageToken': 'two'})]
        with self.assertRaisesRegex(GoogleError, 'page_limit'):
            self.calendar.list_events()

    def test_library_debug_messages_and_payloads_are_suppressed(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        root = logging.getLogger()
        original_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, original_level)

        def request(*arguments, **keywords):
            logging.getLogger('urllib3').error('SENTINEL_ACCESS SENTINEL_EVENT_BODY')
            raise RuntimeError('SENTINEL_ACCESS')

        self.session.get.side_effect = request
        with self.assertRaises(GoogleError) as caught:
            self.calendar.list_events()
        self.assertNotIn('SENTINEL', str(caught.exception))
        self.assertEqual(output.getvalue(), '')
