from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from calendar_sync.caldav import CalDAVError, CalDAVPreconditionError, CalendarResource, _resource
from calendar_sync.events import normalize_google_event
from calendar_sync.executor import ExecutionError, ExecutionStopped, execute_sync
from calendar_sync.planner import PlanContext, event_identity, serialize_owned_event
from calendar_sync.state import StateStore


URL = 'https://nextcloud.tcadc.com/remote.php/dav/calendars/test/execution/'


def source_event(source_id='source'):
    return {'id': source_id, 'summary': 'Example', 'start': {'dateTime': '2026-11-08T09:00:00Z'},
            'end': {'dateTime': '2026-11-08T10:00:00Z'}}


class MemoryCalDAV:
    calendar_url = URL

    def __init__(self):
        self.resources = {}
        self.puts = []

    def list_resources(self):
        return list(self.resources.values())

    def get_resource(self, href):
        return self.resources.get(href)

    def put_resource(self, href, payload, *, create=False, expected_etag=None, allow_write=False):
        if allow_write is not True:
            raise AssertionError('writes not enabled')
        current = self.resources.get(href)
        self.puts.append((href, create, expected_etag, payload))
        if (create and current is not None) or (not create and (current is None or current.etag != expected_etag)):
            raise CalDAVPreconditionError('caldav_write_conflict')
        self.resources[href] = _resource(href, '"version-' + str(len(self.puts)) + '"', payload.decode())


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'state.sqlite3'
        self.store = StateStore(self.path).__enter__()
        self.addCleanup(lambda: self.store.close())
        self.store.create_configuration('config', 'operator', 'master@example.invalid', URL,
                                        google_credential_file='/unused-google', nextcloud_credential_file='/unused-nextcloud')
        self.google = Mock(calendar_id='master@example.invalid')
        self.google.list_events.return_value = [source_event()]
        self.caldav = MemoryCalDAV()

    def execute(self, **kwargs):
        return execute_sync(self.store, 'config', self.google, self.caldav, **kwargs)

    def mapping(self):
        return self.store.get_mapping('config', 'source')

    def resource(self):
        return next(iter(self.caldav.resources.values()))

    def restart(self):
        self.store.close()
        self.store = StateStore(self.path).__enter__()

    def crash_after_put(self):
        original = self.caldav.put_resource
        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt()
        return patch.object(self.caldav, 'put_resource', side_effect=crash)

    def test_default_dry_run_has_no_put_intent_run_or_mapping(self):
        report = self.execute()
        self.assertTrue(report.dry_run)
        self.assertEqual(report.plan.counts()['create'], 1)
        self.assertEqual(self.caldav.puts, [])
        self.assertEqual(self.store.pending_intents('config'), ())
        for table in ('runs', 'audit_entries', 'mappings', 'write_intents'):
            self.assertEqual(self.store.connection.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)

    def test_dry_run_then_live_then_repeat_creates_one_resource_and_mapping(self):
        self.execute()
        first = self.execute(dry_run=False)
        second = self.execute(dry_run=False)
        self.assertEqual(first.counts()['applied'], 1)
        self.assertEqual(second.counts()['unchanged'], 1)
        self.assertEqual(len(self.caldav.puts), 1)
        self.assertEqual(len(self.caldav.resources), 1)
        self.assertEqual(self.mapping()['destination_etag'], self.resource().etag)
        self.assertEqual(self.store.pending_intents('config'), ())

    def test_source_edit_updates_same_identity_using_saved_etag(self):
        self.execute(dry_run=False)
        previous = dict(self.mapping())
        self.google.list_events.return_value[0]['summary'] = 'Changed'
        report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['applied'], 1)
        self.assertFalse(self.caldav.puts[-1][1])
        self.assertEqual(self.caldav.puts[-1][2], previous['destination_etag'])
        self.assertEqual(self.mapping()['destination_uid'], previous['destination_uid'])
        self.assertNotEqual(self.mapping()['source_fingerprint'], previous['source_fingerprint'])

    def test_destination_edit_after_plan_is_detected_before_put(self):
        self.execute(dry_run=False)
        self.google.list_events.return_value[0]['summary'] = 'Changed'
        before = dict(self.mapping())
        original = self.caldav.get_resource
        def edited(href):
            return replace(original(href), etag='"user-edited"')
        with patch.object(self.caldav, 'get_resource', side_effect=edited):
            report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['conflict'], 1)
        self.assertEqual(len(self.caldav.puts), 1)
        self.assertEqual(dict(self.mapping()), before)

    def test_412_race_after_fresh_get_never_overwrites_user_edit(self):
        self.execute(dry_run=False)
        before = dict(self.mapping())
        self.google.list_events.return_value[0]['summary'] = 'Changed'
        original = self.caldav.put_resource
        def race(href, *args, **kwargs):
            self.caldav.resources[href] = replace(self.caldav.resources[href], etag='"race"')
            original(href, *args, **kwargs)
        with patch.object(self.caldav, 'put_resource', side_effect=race):
            report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['conflict'], 1)
        self.assertEqual(dict(self.mapping()), before)
        self.assertEqual(self.resource().etag, '"race"')

    def test_unowned_collision_never_puts_or_adopts(self):
        context = PlanContext('config', self.google.calendar_id, URL)
        event = normalize_google_event(source_event()).event
        identity = event_identity(context, event.source_id)
        self.caldav.resources[identity.href] = _resource(identity.href, '"foreign"',
            serialize_owned_event(context, event, stamp=datetime.now(timezone.utc)).decode())
        report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['conflict'], 1)
        self.assertEqual(self.caldav.puts, [])
        self.assertIsNone(self.mapping())

    def test_create_race_after_plan_preserves_foreign_resource(self):
        original = self.caldav.put_resource
        def race(href, payload, **kwargs):
            self.caldav.resources[href] = CalendarResource(href, '"foreign"', 'foreign', b'FOREIGN', None)
            original(href, payload, **kwargs)
        with patch.object(self.caldav, 'put_resource', side_effect=race):
            report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['conflict'], 1)
        self.assertEqual(self.resource().data, b'FOREIGN')
        self.assertIsNone(self.mapping())

    def test_timeout_after_remote_create_is_confirmed_without_second_put(self):
        original = self.caldav.put_resource
        def timeout(*args, **kwargs):
            original(*args, **kwargs)
            raise CalDAVError('caldav_write_uncertain')
        with patch.object(self.caldav, 'put_resource', side_effect=timeout):
            report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['applied'], 1)
        self.assertEqual(len(self.caldav.puts), 1)
        self.assertIsNotNone(self.mapping())

    def test_crash_after_remote_create_recovers_after_database_reopen_without_put(self):
        with self.crash_after_put(), self.assertRaises(KeyboardInterrupt):
            self.execute(dry_run=False)
        self.assertIsNone(self.mapping())
        self.assertEqual(len(self.store.pending_intents('config')), 1)
        self.restart()
        report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['applied'], 1)
        self.assertEqual(len(self.caldav.puts), 1)
        self.assertIsNotNone(self.mapping())
        self.assertEqual(self.store.connection.execute(
            "SELECT count(*) FROM runs WHERE status='interrupted'"
        ).fetchone()[0], 1)

    def test_crash_after_remote_update_recovers_new_fingerprint_without_put(self):
        self.execute(dry_run=False)
        old = dict(self.mapping())
        self.google.list_events.return_value[0]['summary'] = 'Changed'
        with self.crash_after_put(), self.assertRaises(KeyboardInterrupt):
            self.execute(dry_run=False)
        self.assertEqual(dict(self.mapping()), old)
        self.restart()
        report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['applied'], 1)
        self.assertEqual(len(self.caldav.puts), 2)
        self.assertNotEqual(self.mapping()['source_fingerprint'], old['source_fingerprint'])

    def test_failed_put_before_apply_retries_only_after_reconciliation(self):
        with patch.object(self.caldav, 'put_resource', side_effect=CalDAVError('caldav_write_uncertain')):
            first = self.execute(dry_run=False)
        self.assertEqual(first.counts()['pending'], 1)
        self.assertIsNone(self.mapping())
        pending = self.store.pending_intents('config')[0]
        second = self.execute(dry_run=False)
        self.assertEqual(second.counts()['applied'], 1)
        self.assertEqual(self.store.get_intent('config', pending['id'])['attempts'], 2)
        self.assertEqual(len(self.caldav.resources), 1)

    def test_missing_success_etag_or_lost_readback_does_not_commit_mapping(self):
        original = self.caldav.get_resource
        calls = 0
        def readback(href):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise CalDAVError('caldav_resource_read_failed')
            return original(href)
        with patch.object(self.caldav, 'get_resource', side_effect=readback):
            report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['pending'], 1)
        self.assertIsNone(self.mapping())
        self.assertEqual(self.execute(dry_run=False).counts()['applied'], 1)
        self.assertEqual(len(self.caldav.puts), 1)

    def test_user_edit_after_uncertain_create_blocks_recovery_without_overwrite(self):
        with self.crash_after_put(), self.assertRaises(KeyboardInterrupt):
            self.execute(dry_run=False)
        resource = self.resource()
        self.caldav.resources[resource.href] = replace(resource, data=resource.data.replace(b'Example', b'Edited'))
        report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['conflict'], 1)
        self.assertEqual(len(self.caldav.puts), 1)
        self.assertIsNone(self.mapping())

    def test_pending_source_change_or_cancellation_is_not_retried(self):
        with patch.object(self.caldav, 'put_resource', side_effect=CalDAVError('caldav_write_uncertain')):
            self.execute(dry_run=False)
        self.google.list_events.return_value = [{'id': 'source', 'status': 'cancelled'}]
        report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['conflict'], 1)
        self.assertEqual(self.caldav.puts, [])
        self.assertIsNone(self.mapping())

    def test_pending_retry_limit_is_durable_across_invocations(self):
        with patch.object(self.caldav, 'put_resource', side_effect=CalDAVError('caldav_write_uncertain')) as put:
            self.execute(dry_run=False, max_put_attempts=1)
            report = self.execute(dry_run=False, max_put_attempts=1)
        self.assertEqual(put.call_count, 1)
        self.assertEqual(report.counts()['pending'], 1)
        self.assertEqual(report.items[0].reason, 'write_retry_limit')
        self.assertEqual(self.store.pending_intents('config')[0]['attempts'], 1)

    def test_dry_run_never_recovers_pending_intent_or_advances_mapping(self):
        with self.crash_after_put(), self.assertRaises(KeyboardInterrupt):
            self.execute(dry_run=False)
        before = self.store.pending_intents('config')
        report = self.execute()
        self.assertTrue(report.dry_run)
        self.assertIsNone(self.mapping())
        self.assertEqual(self.store.pending_intents('config'), before)
        self.assertEqual(len(self.caldav.puts), 1)

    def test_missing_cancelled_and_recurring_sources_never_delete_destination(self):
        self.execute(dry_run=False)
        before = dict(self.mapping())
        for sources in ([], [{'id': 'source', 'status': 'cancelled'}],
                        [{**source_event(), 'recurrence': ['RRULE:FREQ=DAILY']}]):
            self.google.list_events.return_value = sources
            self.execute(dry_run=False)
            self.assertEqual(dict(self.mapping()), before)
            self.assertEqual(len(self.caldav.resources), 1)
        self.assertEqual(len(self.caldav.puts), 1)

    def test_duplicate_or_partial_preflight_stops_before_run_and_put(self):
        self.google.list_events.return_value = [source_event(), source_event()]
        with self.assertRaises(Exception):
            self.execute(dry_run=False)
        self.assertEqual(self.store.connection.execute('SELECT count(*) FROM runs').fetchone()[0], 0)
        self.assertEqual(self.caldav.puts, [])
        self.google.list_events.side_effect = CalDAVError('fake read failed')
        with self.assertRaises(CalDAVError):
            self.execute(dry_run=False)
        self.assertEqual(self.caldav.puts, [])

    def test_atomic_finalize_failure_leaves_journal_for_recovery(self):
        self.store.connection.execute(
            "CREATE TRIGGER fail_applied BEFORE INSERT ON audit_entries WHEN NEW.outcome='applied' "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
        with self.assertRaises(ExecutionError):
            self.execute(dry_run=False)
        self.assertIsNone(self.mapping())
        self.assertEqual(len(self.store.pending_intents('config')), 1)
        self.assertEqual(self.store.connection.execute(
            "SELECT count(*) FROM audit_entries WHERE outcome='applied'"
        ).fetchone()[0], 0)
        self.store.connection.execute('DROP TRIGGER fail_applied')
        self.assertEqual(self.execute(dry_run=False).counts()['applied'], 1)
        self.assertEqual(len(self.caldav.puts), 1)

    def test_successful_intent_is_not_reapplied_after_run_finish_crash(self):
        original = self.store.finish_run
        def crash(*args, **kwargs):
            if args[2] == 'succeeded':
                raise KeyboardInterrupt()
            return original(*args, **kwargs)
        with patch.object(self.store, 'finish_run', side_effect=crash), self.assertRaises(KeyboardInterrupt):
            self.execute(dry_run=False)
        self.restart()
        self.assertEqual(self.execute(dry_run=False).counts()['unchanged'], 1)
        self.assertEqual(len(self.caldav.puts), 1)

    def test_one_event_failure_does_not_prevent_other_source_success(self):
        self.google.list_events.return_value = [source_event('first'), source_event('second')]
        original = self.caldav.put_resource
        calls = 0
        def partial(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise CalDAVError('caldav_write_uncertain')
            return original(*args, **kwargs)
        with patch.object(self.caldav, 'put_resource', side_effect=partial):
            report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['applied'], 1)
        self.assertEqual(report.counts()['pending'], 1)
        self.assertEqual(report.status, 'failed')

    def test_invalid_live_options_and_report_repr_do_not_expose_private_data(self):
        for options in ({'dry_run': 'false'}, {'max_put_attempts': 0}, {'max_put_attempts': True}):
            with self.assertRaises(ExecutionError):
                self.execute(**options)
        self.google.list_events.return_value[0]['summary'] = 'SENTINEL_PRIVATE'
        report = self.execute(dry_run=False)
        self.assertNotIn('SENTINEL', repr(report))
        self.assertNotIn('SENTINEL', repr(report.items[0]))

    def test_payload_hash_mismatch_prevents_retry_after_serializer_change(self):
        with patch.object(self.caldav, 'put_resource', side_effect=CalDAVError('caldav_write_uncertain')):
            self.execute(dry_run=False)
        with patch('calendar_sync.executor.serialize_owned_event', return_value=b'changed serialization'):
            report = self.execute(dry_run=False)
        self.assertEqual(report.counts()['conflict'], 1)
        self.assertEqual(report.items[0].reason, 'payload_reconstruction_changed')
        self.assertEqual(self.caldav.puts, [])

    def test_source_edit_or_cancellation_after_destination_get_blocks_put(self):
        original = self.caldav.get_resource
        for changed in ({**source_event(), 'summary': 'Edited'}, {'id': 'source', 'status': 'cancelled'}):
            self.google.list_events.return_value = [source_event()]
            def change_source(href):
                self.google.list_events.return_value = [changed]
                return original(href)
            with patch.object(self.caldav, 'get_resource', side_effect=change_source):
                report = self.execute(dry_run=False)
            self.assertEqual(report.counts()['conflict'], 1)
            self.assertEqual(report.items[0].reason, 'source_changed_during_recovery')
            self.assertIsNone(self.mapping())
            self.assertEqual(self.caldav.puts, [])

    def test_source_revalidation_outage_or_duplicate_blocks_put_and_keeps_intent(self):
        for failed in (CalDAVError('fake outage'), [source_event(), source_event()]):
            self.google.list_events.side_effect = [[source_event()], failed, [source_event()]]
            report = self.execute(dry_run=False)
            self.assertEqual(report.counts()['pending'], 1)
            self.assertEqual(report.items[0].reason, 'source_revalidation_failed')
            self.assertEqual(self.caldav.puts, [])
            self.assertIsNone(self.mapping())
            self.google.list_events.side_effect = None

    def test_stop_before_preflight_does_not_read_or_create_run(self):
        with self.assertRaises(ExecutionStopped):
            self.execute(dry_run=False, should_stop=lambda: True)
        self.google.list_events.assert_not_called()
        self.assertEqual(self.store.connection.execute('SELECT count(*) FROM runs').fetchone()[0], 0)

    def test_stop_after_google_preflight_does_not_start_nextcloud_read(self):
        stopping = False
        def source():
            nonlocal stopping
            stopping = True
            return [source_event()]
        self.google.list_events.side_effect = source
        with patch.object(self.caldav, 'list_resources') as read:
            with self.assertRaises(ExecutionStopped):
                self.execute(dry_run=False, should_stop=lambda: stopping)
            read.assert_not_called()
        self.assertEqual(self.store.connection.execute('SELECT count(*) FROM runs').fetchone()[0], 0)

    def test_stop_during_source_revalidation_keeps_unattempted_intent_and_no_put(self):
        stopping = False
        calls = 0
        def source():
            nonlocal stopping, calls
            calls += 1
            if calls == 2:
                stopping = True
            return [source_event()]
        self.google.list_events.side_effect = source
        with self.assertRaises(ExecutionStopped):
            self.execute(dry_run=False, should_stop=lambda: stopping)
        self.assertEqual(self.caldav.puts, [])
        self.assertEqual(self.store.pending_intents('config')[0]['attempts'], 0)
        self.assertEqual(self.store.connection.execute('SELECT status FROM runs').fetchone()[0], 'interrupted')

    def test_stop_after_put_confirms_first_write_before_stopping_remaining_events(self):
        self.google.list_events.return_value = [source_event('first'), source_event('second')]
        stopping = False
        original = self.caldav.put_resource
        def stop_after_write(*args, **kwargs):
            nonlocal stopping
            original(*args, **kwargs)
            stopping = True
        with patch.object(self.caldav, 'put_resource', side_effect=stop_after_write):
            with self.assertRaises(ExecutionStopped):
                self.execute(dry_run=False, should_stop=lambda: stopping)
        self.assertEqual(len(self.caldav.puts), 1)
        self.assertIsNotNone(self.store.get_mapping('config', 'first'))
        self.assertIsNone(self.store.get_mapping('config', 'second'))

    def test_rate_limited_put_defers_readback_and_other_events_until_next_invocation(self):
        self.google.list_events.return_value = [source_event('first'), source_event('second')]
        def limited(*args, **kwargs):
            self.caldav.retry_after_seconds = 120
            raise CalDAVError('caldav_rate_limited')
        with patch.object(self.caldav, 'put_resource', side_effect=limited) as put:
            with patch.object(self.caldav, 'get_resource', wraps=self.caldav.get_resource) as get:
                with self.assertRaisesRegex(ExecutionError, '^execution_rate_limited$'):
                    self.execute(dry_run=False)
                self.assertEqual(put.call_count, 1)
                self.assertEqual(get.call_count, 1)
        self.assertEqual(len(self.store.pending_intents('config')), 1)
        self.assertEqual(self.store.connection.execute('SELECT count(*) FROM mappings').fetchone()[0], 0)
        self.assertEqual(self.execute(dry_run=False).counts()['applied'], 2)
