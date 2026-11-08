from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from calendar_sync.schema import MIGRATIONS
from calendar_sync.state import StateConflictError, StateStore


class JournalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'state.sqlite3'

    def configure(self, store, name='config'):
        store.create_configuration(name, 'operator', 'google', 'https://example.invalid/calendar/',
            google_credential_file='/unused/google', nextcloud_credential_file='/unused/password')

    def intent(self):
        return dict(id=str(uuid4()), source_event_id='source', action='create',
            destination_href='event.ics', destination_uid='uid', source_fingerprint='a'*64,
            owner_marker='b'*64, payload_hash='c'*64, stamp='2026-11-08T00:00:00+00:00',
            expected_etag=None, baseline_fingerprint=None)

    def test_v1_upgrade_preserves_mapping_run_and_append_only_audit(self):
        with patch('calendar_sync.state.MIGRATIONS', MIGRATIONS[:1]):
            with StateStore(self.path) as store:
                self.configure(store)
                with store.transaction() as connection:
                    connection.execute("INSERT INTO runs(id,configuration_id,dry_run) VALUES ('old-run','config',0)")
                    connection.execute("INSERT INTO mappings(configuration_id,source_event_id,destination_href,"
                        "destination_uid,source_fingerprint,destination_etag,applied_run_id) "
                        "VALUES ('config','source','event.ics','uid','old','\"old\"','old-run')")
                    for action, outcome in (('create', 'applied'), ('run', 'succeeded')):
                        connection.execute('INSERT INTO audit_entries(configuration_id,run_id,action,outcome) '
                            "VALUES ('config','old-run',?,?)", (action, outcome))
                    connection.execute("UPDATE runs SET status='succeeded' WHERE id='old-run'")
        with StateStore(self.path) as store:
            self.assertEqual(store.connection.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(store.get_mapping('config', 'source')['source_fingerprint'], 'old')
            self.assertEqual(len(store.list_audit('config')), 2)
            self.assertEqual(store.connection.execute('SELECT purpose FROM runs').fetchone()[0], 'manual')
            self.assertEqual(store.pending_intents('config'), ())
            with self.assertRaises(sqlite3.IntegrityError):
                store.connection.execute('DELETE FROM audit_entries')

    def test_intent_requires_active_live_execution_and_valid_hashes(self):
        with StateStore(self.path) as store:
            self.configure(store)
            for options in ({}, {'dry_run': False}, {'purpose': 'execution'}):
                run = store.start_run('config', **options)
                with self.assertRaises(ValueError):
                    store.prepare_intent('config', run, self.intent())
            run = store.start_run('config', dry_run=False, purpose='execution')
            bad = self.intent() | {'payload_hash': 'invalid'}
            with self.assertRaises(ValueError):
                store.prepare_intent('config', run, bad)
            store.finish_run('config', run, 'succeeded')
            with self.assertRaises(ValueError):
                store.prepare_intent('config', run, self.intent())
            self.assertEqual(store.pending_intents('config'), ())

    def test_pending_uniqueness_identity_immutability_and_retention(self):
        with StateStore(self.path) as store:
            self.configure(store)
            run = store.start_run('config', dry_run=False, purpose='execution')
            intent = self.intent()
            store.prepare_intent('config', run, intent)
            with self.assertRaises(sqlite3.IntegrityError):
                store.prepare_intent('config', run, self.intent())
            for sql in ("UPDATE write_intents SET payload_hash='changed'", 'DELETE FROM write_intents'):
                with self.assertRaises(sqlite3.IntegrityError):
                    store.connection.execute(sql)
            self.assertTrue(store.mark_intent_attempt('config', intent['id'], 1))
            self.assertFalse(store.mark_intent_attempt('config', intent['id'], 1))
            self.assertEqual(store.get_intent('config', intent['id'])['attempts'], 1)

    def test_cross_configuration_audit_cannot_reference_foreign_intent(self):
        with StateStore(self.path) as store:
            self.configure(store)
            self.configure(store, 'other')
            run = store.start_run('config', dry_run=False, purpose='execution')
            other = store.start_run('other', dry_run=False, purpose='execution')
            intent = self.intent()
            store.prepare_intent('config', run, intent)
            self.assertIsNone(store.get_intent('other', intent['id']))
            with self.assertRaises(sqlite3.IntegrityError):
                store.connection.execute('INSERT INTO audit_entries '
                    '(configuration_id,run_id,action,outcome,operation_id) VALUES (?,?,?,?,?)',
                    ('other', other, 'create', 'failed', intent['id']))
            with self.assertRaises(StateConflictError):
                store.complete_intent('other', intent['id'], other, '"etag"')

    def test_local_mapping_changed_blocks_atomic_finalization(self):
        with StateStore(self.path) as store:
            self.configure(store)
            run = store.start_run('config', dry_run=False, purpose='execution')
            intent = self.intent()
            store.prepare_intent('config', run, intent)
            store.record_applied('config', run, source_event_id='source', destination_href='event.ics',
                destination_uid='uid', source_fingerprint='existing', destination_etag='"existing"', action='create')
            with self.assertRaises(StateConflictError):
                store.complete_intent('config', intent['id'], run, '"new"')
            self.assertEqual(store.get_mapping('config', 'source')['destination_etag'], '"existing"')
            self.assertEqual(store.get_intent('config', intent['id'])['status'], 'pending')
            self.assertEqual(len(store.list_audit('config')), 1)

    def test_restart_interrupts_execution_only_and_is_configuration_scoped(self):
        with StateStore(self.path) as store:
            self.configure(store)
            self.configure(store, 'other')
            manual = store.start_run('config')
            execution = store.start_run('config', dry_run=False, purpose='execution')
            other = store.start_run('other', dry_run=False, purpose='execution')
            store.interrupt_execution_runs('config')
            rows = dict(store.connection.execute('SELECT id,status FROM runs'))
            self.assertEqual(rows, {manual: 'running', execution: 'interrupted', other: 'running'})
            self.assertEqual(store.list_audit('config')[0]['reason_code'], 'execution_restarted')
