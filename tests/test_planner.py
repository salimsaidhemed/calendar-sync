from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from icalendar import Calendar

from calendar_sync.caldav import CalendarResource
from calendar_sync.events import normalize_google_event, serialize_event
from calendar_sync.planner import (
    BASELINE, OWNER, SOURCE, MappingSnapshot, PlanContext, PlanItem, PlanningError, SyncPlan,
    build_plan, event_identity, preview_sync, serialize_owned_event,
)
from calendar_sync.state import StateStore


URL = 'https://nextcloud.tcadc.com/remote.php/dav/calendars/test/uat/'


def source_event(source_id='source'):
    return {'id': source_id, 'summary': 'Example',
            'start': {'dateTime': '2026-11-07T09:00:00Z'},
            'end': {'dateTime': '2026-11-07T10:00:00Z'}}


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.context = PlanContext('primary-config', 'master@example.invalid', URL)
        self.source = source_event()
        self.event = normalize_google_event(self.source).event
        self.identity = event_identity(self.context, 'source')
        self.stamp = datetime(2026, 11, 7, tzinfo=timezone.utc)
        self.mapping = MappingSnapshot(self.context.configuration_id, 'source', self.identity.href,
                                       self.identity.uid, self.event.fingerprint(), '"etag"')
        self.resource = CalendarResource(self.identity.href, '"etag"', self.identity.uid,
                                         serialize_owned_event(self.context, self.event, stamp=self.stamp), None)

    def plan(self, sources=None, resources=(), mappings=(), **kwargs):
        return build_plan(self.context, [self.source] if sources is None else sources,
                          resources, mappings, source_complete=True, destination_complete=True, **kwargs)

    def test_new_source_plans_create_with_stable_identity_and_no_input_mutation(self):
        original = deepcopy(self.source)
        first, second = self.plan(), self.plan()
        self.assertEqual(first, second)
        self.assertTrue(first.dry_run)
        self.assertEqual(first.counts()['create'], 1)
        self.assertEqual(first.items[0].destination_href, self.identity.href)
        self.assertIsNone(first.items[0].expected_etag)
        self.assertEqual(self.source, original)
        self.assertFalse(hasattr(first, 'execute'))

    def test_identity_is_configuration_calendar_and_source_scoped(self):
        for context, source_id in (
            (replace(self.context, configuration_id='other'), 'source'),
            (replace(self.context, google_calendar_id='other@example.invalid'), 'source'),
            (replace(self.context, destination_calendar_url=URL + 'other/'), 'source'),
            (self.context, 'different-source'),
        ):
            self.assertNotEqual(event_identity(context, source_id).uid, self.identity.uid)
        changed = {**self.source, 'summary': 'Changed'}
        self.assertEqual(self.plan([changed]).items[0].destination_uid, self.identity.uid)

    def test_incomplete_snapshots_never_produce_partial_plans(self):
        for source_complete, destination_complete in ((False, True), (True, False), ('true', True)):
            with self.assertRaisesRegex(PlanningError, '^planner_incomplete_snapshot$'):
                build_plan(self.context, [], [], source_complete=source_complete,
                           destination_complete=destination_complete)

    def test_matching_mapping_markers_content_and_etag_plan_unchanged(self):
        plan = self.plan(resources=[self.resource], mappings=[self.mapping])
        self.assertEqual(plan.counts()['unchanged'], 1)
        self.assertEqual(plan.items[0].expected_etag, '"etag"')

    def test_changed_source_plans_update_only_after_owned_baseline_checks(self):
        self.source['summary'] = 'Changed'
        item = self.plan(resources=[self.resource], mappings=[self.mapping]).items[0]
        self.assertEqual((item.action, item.reason), ('update', 'source_changed'))
        self.assertEqual(item.expected_etag, '"etag"')
        self.assertNotEqual(item.source_fingerprint, self.mapping.source_fingerprint)

    def test_changed_destination_etag_conflicts_even_when_source_is_unchanged(self):
        resource = replace(self.resource, etag='"edited"')
        item = self.plan(resources=[resource], mappings=[self.mapping]).items[0]
        self.assertEqual((item.action, item.reason), ('conflict', 'destination_etag_changed'))

    def test_unmapped_existing_resource_is_never_adopted_even_with_valid_markers(self):
        item = self.plan(resources=[self.resource]).items[0]
        self.assertEqual((item.action, item.reason), ('conflict', 'mapping_missing_or_identity_collision'))
        unmarked = replace(self.resource, data=serialize_event(self.event, self.identity.uid, stamp=self.stamp))
        self.assertEqual(self.plan(resources=[unmarked]).items[0].action, 'conflict')

    def test_href_and_uid_collisions_never_plan_create(self):
        for resource in (replace(self.resource, uid='foreign-uid'),
                         replace(self.resource, href=URL + 'other.ics')):
            self.assertEqual(self.plan(resources=[resource]).items[0].action, 'conflict')
        reserved = replace(self.mapping, source_event_id='other-source')
        plan = self.plan(mappings=[reserved])
        self.assertEqual(plan.counts()['create'], 0)
        self.assertEqual(plan.counts()['conflict'], 1)

    def test_matching_titles_dates_are_not_used_to_adopt_unowned_events(self):
        resource = replace(self.resource, uid='foreign-uid', href=URL + 'foreign.ics',
                           data=serialize_event(self.event, 'foreign-uid', stamp=self.stamp))
        self.assertEqual(self.plan(resources=[resource]).items[0].action, 'create')

    def test_missing_destination_or_changed_mapping_identity_conflicts(self):
        self.assertEqual(self.plan(mappings=[self.mapping]).items[0].reason, 'destination_missing')
        altered = replace(self.mapping, destination_uid='old-uid')
        self.assertEqual(self.plan(mappings=[altered]).items[0].reason, 'mapping_identity_mismatch')
        resource = replace(self.resource, uid='replaced-uid')
        self.assertEqual(self.plan(resources=[resource], mappings=[self.mapping]).items[0].reason,
                         'destination_identity_mismatch')

    def test_missing_cancelled_and_recurring_sources_never_generate_delete(self):
        for sources, action, reason in (
            ([], 'missing', 'source_absent'),
            ([{'id': 'source', 'status': 'cancelled'}], 'missing', 'cancelled'),
            ([{**self.source, 'recurrence': ['RRULE:FREQ=DAILY']}], 'unsupported', 'unsupported_recurrence'),
        ):
            plan = self.plan(sources, [self.resource], [self.mapping])
            self.assertEqual(len(plan.items), 1)
            self.assertEqual((plan.items[0].action, plan.items[0].reason), (action, reason))
            self.assertNotIn('delete', plan.counts())
        self.assertEqual(self.plan([{'id': 'new', 'status': 'cancelled'}]).items[0].action, 'unsupported')

    def test_unsupported_destination_is_never_overwritten(self):
        resource = replace(self.resource, skip_reason='unsupported_recurrence')
        self.assertEqual(self.plan(resources=[resource], mappings=[self.mapping]).items[0].reason,
                         'destination_unsupported')

    def test_ownership_markers_and_real_content_must_match_saved_baseline(self):
        for field in (OWNER, SOURCE, BASELINE, 'SUMMARY'):
            calendar = Calendar.from_ical(self.resource.data)
            calendar.walk('VEVENT')[0][field] = 'SENTINEL_CHANGED'
            resource = replace(self.resource, data=calendar.to_ical())
            item = self.plan(resources=[resource], mappings=[self.mapping]).items[0]
            self.assertEqual((item.action, item.reason), ('conflict', 'ownership_or_baseline_mismatch'))
        for field, value in (('METHOD', 'REQUEST'), ('VERSION', '1.0')):
            calendar = Calendar.from_ical(self.resource.data)
            calendar[field] = value
            resource = replace(self.resource, data=calendar.to_ical())
            self.assertEqual(self.plan(resources=[resource], mappings=[self.mapping]).items[0].action, 'conflict')

    def test_extra_unsupported_properties_or_invalid_owned_payload_conflict(self):
        calendar = Calendar.from_ical(self.resource.data)
        calendar.walk('VEVENT')[0].add('attendee', 'mailto:private@example.invalid')
        for data in (calendar.to_ical(), b'SENTINEL_INVALID'):
            item = self.plan(resources=[replace(self.resource, data=data)], mappings=[self.mapping]).items[0]
            self.assertEqual(item.reason, 'ownership_or_baseline_mismatch')

    def test_duplicate_sources_destinations_and_mappings_fail_closed(self):
        for sources, resources, mappings in (
            ([self.source, self.source], [], []),
            ([self.source], [self.resource, self.resource], []),
            ([self.source], [], [self.mapping, self.mapping]),
        ):
            with self.assertRaises(PlanningError):
                self.plan(sources, resources, mappings)

    def test_out_of_scope_resources_and_mappings_are_rejected(self):
        with self.assertRaises(PlanningError):
            self.plan(resources=[replace(self.resource, href='https://evil.invalid/event.ics')])
        with self.assertRaises(PlanningError):
            self.plan(resources=[replace(self.resource, etag='W/"weak"')])
        for mapping in (replace(self.mapping, configuration_id='other'),
                        replace(self.mapping, destination_href='https://evil.invalid/event.ics'),
                        replace(self.mapping, source_fingerprint='invalid')):
            with self.assertRaises(PlanningError):
                self.plan(mappings=[mapping])

    def test_plan_order_is_deterministic_and_omissions_are_reported(self):
        second = source_event('second')
        second['attendees'] = [{'email': 'SENTINEL'}]
        first = self.plan([self.source, second])
        self.assertEqual(first, self.plan([second, self.source]))
        item = next(item for item in first.items if item.source_id == 'second')
        self.assertEqual(item.omitted_fields, ('attendees',))
        self.assertNotIn('SENTINEL', repr(first))
        self.assertNotIn('SENTINEL', repr(item))

    def test_invalid_sources_and_contexts_produce_safe_failures(self):
        self.assertEqual(self.plan([None]).items[0].action, 'unsupported')
        for configuration, calendar, url in (('', 'id', URL), ('id', 'primary', URL),
                                              ('id', 'id', 'http://evil.invalid/cal/')):
            with self.assertRaises(PlanningError):
                PlanContext(configuration, calendar, url)
        with self.assertRaises(PlanningError):
            SyncPlan(self.context, (), dry_run=False)
        with self.assertRaises(PlanningError):
            PlanItem('delete', 'not_allowed', 'source')

    def test_all_day_owned_payload_and_marker_hashes_roundtrip(self):
        source = {**self.source, 'start': {'date': '2026-11-07'}, 'end': {'date': '2026-11-08'}}
        event = normalize_google_event(source).event
        mapping = replace(self.mapping, source_fingerprint=event.fingerprint())
        data = serialize_owned_event(self.context, event, stamp=self.stamp)
        resource = replace(self.resource, data=data)
        self.assertEqual(self.plan([source], [resource], [mapping]).items[0].action, 'unchanged')
        component = Calendar.from_ical(data).walk('VEVENT')[0]
        self.assertEqual(str(component[OWNER]), self.context.owner_marker())
        self.assertEqual(str(component[SOURCE]), self.identity.source_marker)
        self.assertNotIn(self.context.google_calendar_id.encode(), data)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = StateStore(Path(self.directory.name) / 'state.sqlite3').__enter__()
        self.addCleanup(self.store.close)
        self.store.create_configuration('config', 'owner', 'master@example.invalid', URL,
                                        google_credential_file='/unused-google', nextcloud_credential_file='/unused-nextcloud')
        self.google = Mock(calendar_id='master@example.invalid')
        self.google.list_events.return_value = [source_event()]
        self.caldav = Mock(calendar_url=URL)
        self.caldav.list_resources.return_value = []

    def test_preview_repeated_runs_do_not_create_runs_audits_or_mappings(self):
        for unused in range(2):
            self.assertEqual(preview_sync(self.store, 'config', self.google, self.caldav).counts()['create'], 1)
        for table in ('runs', 'audit_entries', 'mappings'):
            self.assertEqual(self.store.connection.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
        self.google.list_events.assert_called()
        self.caldav.list_resources.assert_called()

    def test_preview_reads_only_configuration_scoped_applied_mappings_without_advancing_them(self):
        context = PlanContext('config', self.google.calendar_id, URL)
        event = normalize_google_event(source_event()).event
        identity = event_identity(context, event.source_id)
        run_id = self.store.start_run('config', dry_run=False)
        self.store.record_applied('config', run_id, source_event_id=event.source_id,
                                  destination_href=identity.href, destination_uid=identity.uid,
                                  source_fingerprint=event.fingerprint(), destination_etag='"etag"', action='create')
        self.store.finish_run('config', run_id, 'succeeded')
        self.store.create_configuration('other', 'other-owner', 'other@example.invalid', URL,
                                        google_credential_file='/unused', nextcloud_credential_file='/unused')
        other_run = self.store.start_run('other', dry_run=False)
        self.store.record_applied('other', other_run, source_event_id='foreign', destination_href=URL + 'foreign.ics',
                                  destination_uid='foreign', source_fingerprint='0' * 64,
                                  destination_etag='"foreign"', action='create')
        self.caldav.list_resources.return_value = [CalendarResource(identity.href, '"etag"', identity.uid,
            serialize_owned_event(context, event, stamp=datetime(2026, 11, 7, tzinfo=timezone.utc)), None)]
        before = self.store.planning_snapshot('config')
        self.assertEqual(len(before[1]), 1)
        self.google.list_events.return_value = [{**source_event(), 'summary': 'Changed'}]
        self.assertEqual(preview_sync(self.store, 'config', self.google, self.caldav).counts()['update'], 1)
        self.assertEqual(self.store.planning_snapshot('config'), before)
        self.assertEqual(len(self.store.list_audit('config')), 2)

    def test_unknown_configuration_or_provider_scope_mismatch_stops_before_reads(self):
        with self.assertRaises(ValueError):
            preview_sync(self.store, 'unknown', self.google, self.caldav)
        self.google.calendar_id = 'other@example.invalid'
        with self.assertRaisesRegex(PlanningError, '^planner_provider_scope_mismatch$'):
            preview_sync(self.store, 'config', self.google, self.caldav)
        self.google.calendar_id = 'master@example.invalid'
        self.caldav.calendar_url = URL + 'other/'
        with self.assertRaisesRegex(PlanningError, '^planner_provider_scope_mismatch$'):
            preview_sync(self.store, 'config', self.google, self.caldav)
        self.google.list_events.assert_not_called()
        self.caldav.list_resources.assert_not_called()

    def test_provider_failure_never_creates_partial_plan_or_applied_state(self):
        self.caldav.list_resources.side_effect = RuntimeError('fake provider failure')
        with self.assertRaisesRegex(PlanningError, '^planner_provider_read_failed$'):
            preview_sync(self.store, 'config', self.google, self.caldav)
        self.caldav.list_resources.reset_mock()
        self.google.list_events.side_effect = RuntimeError('SENTINEL_UNKNOWN_PROVIDER')
        with self.assertRaisesRegex(PlanningError, '^planner_provider_read_failed$'):
            preview_sync(self.store, 'config', self.google, self.caldav)
        self.caldav.list_resources.assert_not_called()
        self.assertEqual(self.store.planning_snapshot('config')[1], ())
        self.assertEqual(self.store.list_audit('config'), [])
