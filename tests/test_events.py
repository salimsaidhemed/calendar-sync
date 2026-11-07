from copy import deepcopy
from datetime import date, datetime, timezone
import unittest

from icalendar import Calendar

from calendar_sync.events import NormalizedEvent, normalize_google_event, serialize_event


def google_event():
    return {'id': 'source', 'summary': 'Example', 'description': 'Details', 'location': 'Room',
            'start': {'dateTime': '2026-11-07T09:00:00+01:00', 'timeZone': 'Europe/Madrid'},
            'end': {'dateTime': '2026-11-07T10:00:00+01:00', 'timeZone': 'Europe/Madrid'}}


class EventTests(unittest.TestCase):
    def setUp(self):
        self.value = google_event()
        self.stamp = datetime(2026, 11, 7, tzinfo=timezone.utc)

    def test_timed_events_canonicalize_to_utc_without_mutating_source(self):
        original = deepcopy(self.value)
        result = normalize_google_event(self.value)
        self.assertEqual(result.reason, 'supported')
        self.assertEqual(result.event.start, datetime(2026, 11, 7, 8, tzinfo=timezone.utc))
        self.assertEqual(self.value, original)

    def test_all_day_dates_preserve_exclusive_end_across_dst(self):
        self.value.update(start={'date': '2026-03-29'}, end={'date': '2026-03-30'})
        event = normalize_google_event(self.value).event
        component = Calendar.from_ical(serialize_event(event, 'uid', stamp=self.stamp)).walk('VEVENT')[0]
        self.assertEqual(component.decoded('DTSTART'), date(2026, 3, 29))
        self.assertEqual(component.decoded('DTEND'), date(2026, 3, 30))

    def test_offset_dst_transition_has_correct_elapsed_time(self):
        self.value.update(start={'dateTime': '2026-03-29T01:30:00+01:00', 'timeZone': 'Europe/Madrid'},
                          end={'dateTime': '2026-03-29T03:30:00+02:00', 'timeZone': 'Europe/Madrid'})
        event = normalize_google_event(self.value).event
        self.assertEqual((event.end - event.start).total_seconds(), 3600)

    def test_named_zone_without_offset_accepts_only_unambiguous_times(self):
        for key in ('start', 'end'):
            self.value[key]['dateTime'] = self.value[key]['dateTime'][:-6]
        self.assertIsNotNone(normalize_google_event(self.value).event)
        for raw in ('2026-03-29T02:30:00', '2026-10-25T02:30:00'):
            self.value['start']['dateTime'] = raw
            self.assertEqual(normalize_google_event(self.value).reason, 'ambiguous_or_nonexistent_time')

    def test_floating_offset_mismatch_unknown_zone_and_subseconds_are_skipped(self):
        cases = (
            ({'dateTime': '2026-11-07T09:00:00'}, 'floating_time'),
            ({'dateTime': '2026-11-07T09:00:00+02:00', 'timeZone': 'Europe/Madrid'}, 'timezone_offset_mismatch'),
            ({'dateTime': '2026-11-07T09:00:00Z', 'timeZone': 'Unknown/Zone'}, 'invalid_time'),
            ({'dateTime': '2026-11-07T09:00:00.0000001Z'}, 'unsupported_subsecond'),
        )
        for start, reason in cases:
            self.value['start'] = start
            self.assertEqual(normalize_google_event(self.value).reason, reason)

    def test_recurrence_masters_instances_exceptions_and_cancelled_are_skipped(self):
        for field in ('recurrence', 'recurringEventId', 'originalStartTime'):
            value = {**self.value, field: []}
            self.assertEqual(normalize_google_event(value).reason, 'unsupported_recurrence')
        self.assertEqual(normalize_google_event({'status': 'cancelled'}).reason, 'cancelled')

    def test_unsupported_types_and_fields_have_safe_reason_codes(self):
        self.assertEqual(normalize_google_event({**self.value, 'eventType': 'birthday'}).reason,
                         'unsupported_event_type')
        self.value.update(attendees=[{'email': 'SENTINEL'}], organizer={'email': 'SENTINEL'}, reminders={})
        result = normalize_google_event(self.value)
        self.assertEqual(result.reason, 'supported_with_omissions')
        self.assertEqual(result.omitted_fields, ('attendees', 'organizer', 'reminders'))
        data = serialize_event(result.event, 'uid', stamp=self.stamp)
        self.assertNotIn(b'ATTENDEE', data)
        self.assertNotIn(b'SENTINEL', data)

    def test_invalid_dates_mixed_types_missing_end_and_nonpositive_duration(self):
        for start, end in (({'date': '2026-02-30'}, {'date': '2026-03-01'}),
                           ({'date': '2026-11-07'}, self.value['end']),
                           (self.value['start'], None),
                           (self.value['start'], self.value['start'])):
            result = normalize_google_event({**self.value, 'start': start, 'end': end})
            self.assertIsNone(result.event)

    def test_invalid_ids_and_text_are_rejected_without_leaking_values(self):
        for fields in ({'id': ''}, {'id': 'bad id'}, {'summary': 'SENTINEL\x00'}):
            result = normalize_google_event({**self.value, **fields})
            self.assertIsNone(result.event)
            self.assertNotIn('SENTINEL', result.reason)
        self.assertEqual(normalize_google_event(None).reason, 'invalid_event')

    def test_serialization_roundtrips_unicode_escaping_and_line_folding(self):
        self.value.update(summary='Café, meeting; ' + 'é' * 100,
                          description='First\r\nBEGIN:VEVENT\nSecond, line; slash \\', location='Room; A')
        event = normalize_google_event(self.value).event
        data = serialize_event(event, 'test-uid', stamp=self.stamp)
        calendar = Calendar.from_ical(data)
        components = calendar.walk('VEVENT')
        self.assertEqual(len(components), 1)
        self.assertEqual(str(components[0]['SUMMARY']), event.summary)
        self.assertEqual(str(components[0]['DESCRIPTION']), event.description)
        self.assertEqual(components[0].decoded('DTSTART'), event.start)
        self.assertTrue(all(len(line) <= 75 for line in data.split(b'\r\n')))

    def test_fingerprints_ignore_key_order_and_omitted_fields_but_track_supported_changes(self):
        event = normalize_google_event(self.value).event
        reordered = dict(reversed(list(self.value.items())))
        reordered['attendees'] = []
        self.assertEqual(event.fingerprint(), normalize_google_event(reordered).event.fingerprint())
        self.value['summary'] = 'Changed'
        self.assertNotEqual(event.fingerprint(), normalize_google_event(self.value).event.fingerprint())

    def test_serialization_requires_safe_uid_and_aware_stamp_and_model(self):
        event = normalize_google_event(self.value).event
        for uid in ('', 'bad\r\nUID:injected', 'a' * 256):
            with self.assertRaises(ValueError):
                serialize_event(event, uid, stamp=self.stamp)
        with self.assertRaises(ValueError):
            serialize_event(event, 'uid', stamp=datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            serialize_event(None, 'uid', stamp=self.stamp)
        with self.assertRaises(ValueError):
            NormalizedEvent('id', '', '', '', datetime(2026, 1, 1), datetime(2026, 1, 2))

    def test_repr_does_not_expose_event_content(self):
        self.value['summary'] = 'SENTINEL'
        result = normalize_google_event(self.value)
        self.assertNotIn('SENTINEL', repr(result))
        self.assertNotIn('SENTINEL', repr(result.event))
