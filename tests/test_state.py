from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from calendar_sync.schema import MIGRATIONS
from calendar_sync.state import SchemaVersionError, StateLockedError, StateStore


class StateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'state.sqlite3'

    def configure(self, store, configuration_id='primary'):
        store.create_configuration(
            configuration_id, 'owner', 'calendar@example.invalid',
            'https://nextcloud.tcadc.com/remote.php/dav/calendars/example/master/',
            google_credential_file='/run/secrets/google.json',
            nextcloud_credential_file='/run/secrets/nextcloud',
        )

    def apply(self, store, run_id, configuration_id='primary', **overrides):
        values = dict(source_event_id='source', destination_href='event.ics',
                      destination_uid='uid', source_fingerprint='fingerprint',
                      destination_etag='"etag"', action='create')
        values.update(overrides)
        store.record_applied(configuration_id, run_id, **values)

    def test_migration_reopen_and_permissions(self):
        with StateStore(self.path) as store:
            self.configure(store)
            run_id = store.start_run('primary', dry_run=False)
            self.apply(store, run_id)
            store.finish_run('primary', run_id, 'succeeded')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with StateStore(self.path) as store:
            self.assertEqual(store.connection.execute('PRAGMA user_version').fetchone()[0], len(MIGRATIONS))
            self.assertEqual(store.connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(store.get_mapping('primary', 'source')['destination_uid'], 'uid')
            self.assertEqual(len(store.list_audit('primary')), 2)

    def test_future_schema_rejected_without_changes_and_lock_released(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute('PRAGMA user_version=999')
        with self.assertRaises(SchemaVersionError):
            with StateStore(self.path):
                pass
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 999)
            connection.execute('PRAGMA user_version=0')
        with StateStore(self.path):
            pass

    def test_failed_migration_rolls_back_schema_and_version(self):
        broken = (MIGRATIONS[0] + ('INVALID SQL',),)
        with patch('calendar_sync.state.MIGRATIONS', broken):
            with self.assertRaises(sqlite3.OperationalError):
                with StateStore(self.path):
                    pass
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0], 0)
        with StateStore(self.path):
            pass

    def test_second_process_cannot_open_state_and_release_allows_reopen(self):
        script = (
            'import sys\n'
            'from calendar_sync.state import StateStore, StateLockedError\n'
            'try:\n'
            '    with StateStore(sys.argv[1]): pass\n'
            'except StateLockedError:\n'
            '    sys.exit(23)\n'
        )
        with StateStore(self.path):
            result = subprocess.run([sys.executable, '-c', script, str(self.path)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 23, result.stderr)
            with self.assertRaises(StateLockedError):
                with StateStore(self.path):
                    pass
        result = subprocess.run([sys.executable, '-c', script, str(self.path)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_incremental_migration_preserves_existing_data(self):
        with StateStore(self.path) as store:
            self.configure(store)
        next_version = MIGRATIONS + (('CREATE TABLE migration_probe(id INTEGER PRIMARY KEY)',),)
        with patch('calendar_sync.state.MIGRATIONS', next_version):
            with StateStore(self.path) as store:
                self.assertEqual(store.connection.execute('PRAGMA user_version').fetchone()[0], len(next_version))
                self.assertEqual(store.connection.execute(
                    'SELECT id FROM configurations'
                ).fetchone()[0], 'primary')
        with self.assertRaises(SchemaVersionError):
            with StateStore(self.path):
                pass

    def test_foreign_keys_reject_missing_configuration_and_cross_scope_run(self):
        with StateStore(self.path) as store:
            with self.assertRaises(sqlite3.IntegrityError):
                store.start_run('missing')
            self.configure(store)
            self.configure(store, 'other')
            run_id = store.start_run('primary', dry_run=False)
            with self.assertRaises(ValueError):
                self.apply(store, run_id, 'other')
            with self.assertRaises(sqlite3.IntegrityError):
                with store.transaction() as connection:
                    connection.execute(
                        'INSERT INTO audit_entries(configuration_id,run_id,action,outcome) '
                        "VALUES (?,?,'create','planned')", ('other', run_id),
                    )

    def test_configuration_isolation_and_paginated_audit(self):
        with StateStore(self.path) as store:
            for configuration_id in ('primary', 'other'):
                self.configure(store, configuration_id)
                run_id = store.start_run(configuration_id, dry_run=False)
                self.apply(store, run_id, configuration_id)
                store.finish_run(configuration_id, run_id, 'succeeded')
            self.assertIsNone(store.get_mapping('missing', 'source'))
            self.assertIsNotNone(store.get_mapping('other', 'source'))
            first = store.list_audit('primary', limit=1)
            second = store.list_audit('primary', after_id=first[0]['id'])
            self.assertEqual(len(second), 1)
            self.assertTrue(all(row['configuration_id'] == 'primary' for row in first + second))
            with self.assertRaises(ValueError):
                store.list_audit('primary', limit=0)

    def test_destination_uniqueness_and_stable_identity(self):
        with StateStore(self.path) as store:
            self.configure(store)
            run_id = store.start_run('primary', dry_run=False)
            self.apply(store, run_id)
            for overrides in (
                dict(source_event_id='second', destination_uid='second-uid'),
                dict(source_event_id='second', destination_href='second.ics'),
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.apply(store, run_id, **overrides)
            with self.assertRaises(ValueError):
                self.apply(store, run_id, destination_href='replacement.ics')
            self.assertEqual(len(store.list_audit('primary')), 1)

    def test_atomic_mapping_and_audit_rollback(self):
        with StateStore(self.path) as store:
            self.configure(store)
            run_id = store.start_run('primary', dry_run=False)
            self.apply(store, run_id)
            store.connection.execute(
                "CREATE TRIGGER fail_audit BEFORE INSERT ON audit_entries "
                "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self.apply(store, run_id, source_fingerprint='changed', action='update')
            self.assertEqual(store.get_mapping('primary', 'source')['source_fingerprint'], 'fingerprint')
            self.assertEqual(len(store.list_audit('primary')), 1)

    def test_dry_run_cannot_create_or_advance_mapping(self):
        with StateStore(self.path) as store:
            self.configure(store)
            dry_run_id = store.start_run('primary')
            store.append_audit('primary', dry_run_id, 'create', 'planned', source_event_id='source')
            with self.assertRaises(ValueError):
                self.apply(store, dry_run_id)
            self.assertIsNone(store.get_mapping('primary', 'source'))
            live_run_id = store.start_run('primary', dry_run=False)
            self.apply(store, live_run_id)
            with self.assertRaises(ValueError):
                self.apply(store, dry_run_id, source_fingerprint='changed', action='update')
            with self.assertRaises(sqlite3.IntegrityError):
                with store.transaction() as connection:
                    connection.execute('UPDATE mappings SET applied_run_id=?', (dry_run_id,))
            self.assertEqual(store.get_mapping('primary', 'source')['source_fingerprint'], 'fingerprint')

    def test_audit_is_append_only(self):
        with StateStore(self.path) as store:
            self.configure(store)
            run_id = store.start_run('primary')
            store.append_audit('primary', run_id, 'unsupported', 'skipped')
            for statement in ('UPDATE audit_entries SET outcome=\'failed\'', 'DELETE FROM audit_entries'):
                with self.assertRaises(sqlite3.IntegrityError):
                    with store.transaction() as connection:
                        connection.execute(statement)
            self.assertEqual(len(store.list_audit('primary')), 1)

    def test_run_completion_prevents_further_writes(self):
        with StateStore(self.path) as store:
            self.configure(store)
            run_id = store.start_run('primary', dry_run=False)
            store.finish_run('primary', run_id, 'interrupted')
            with self.assertRaises(ValueError):
                self.apply(store, run_id)
            with self.assertRaises(ValueError):
                store.finish_run('primary', run_id, 'succeeded')
            with self.assertRaises(ValueError):
                store.append_audit('primary', run_id, 'create', 'planned')

    def test_configuration_rollback_and_transaction_contract(self):
        with StateStore(self.path) as store:
            with self.assertRaises(ValueError):
                store.create_configuration('primary', 'owner', 'google', 'url',
                                           google_credential_file='relative.json',
                                           nextcloud_credential_file='/run/secrets/nextcloud')
            self.configure(store)
            with self.assertRaises(RuntimeError):
                with store.transaction() as connection:
                    connection.execute("UPDATE configurations SET owner_subject='changed'")
                    with store.transaction():
                        pass
            self.assertEqual(store.connection.execute(
                'SELECT owner_subject FROM configurations'
            ).fetchone()[0], 'owner')
