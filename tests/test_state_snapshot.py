from contextlib import closing, redirect_stdout
import fcntl
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from calendar_sync.schema import MIGRATIONS
from calendar_sync.state import StateStore
from scripts.state_snapshot import SnapshotError, main, snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.source = self.root/'source.sqlite3'
        self.backup = self.root/'backups'/'backup.sqlite3'
        with StateStore(self.source) as store:
            store.create_configuration('test', 'operator', 'google@example.invalid', 'https://example.invalid/calendar/',
                google_credential_file='/private/google', nextcloud_credential_file='/private/nextcloud')
            run = store.start_run('test', dry_run=False)
            store.record_applied('test', run, source_event_id='source', destination_href='event.ics', destination_uid='uid',
                source_fingerprint='fingerprint', destination_etag='"etag"', action='create')
            store.finish_run('test', run, 'succeeded')
            execution = store.start_run('test', dry_run=False, purpose='execution')
            operation = str(uuid4())
            store.prepare_intent('test', execution, dict(id=operation, source_event_id='pending-source', action='create',
                destination_href='pending.ics', destination_uid='pending-uid', source_fingerprint='a'*64,
                owner_marker='b'*64, payload_hash='c'*64, stamp='2026-11-15T00:00:00+00:00',
                expected_etag=None, baseline_fingerprint=None))
            store.mark_intent_attempt('test', operation, 3)

    def contents(self, path):
        with closing(sqlite3.connect(path)) as connection:
            return '\n'.join(connection.iterdump())

    def test_backup_restore_preserves_all_tables_schema_and_audit_triggers(self):
        before = self.contents(self.source)
        self.assertEqual(snapshot(self.source, self.backup), (2, 1))
        restored = self.root/'restored'/'state.sqlite3'
        self.assertEqual(snapshot(self.backup, restored), (2, 1))
        self.assertEqual(self.contents(restored), before)
        self.assertEqual(self.contents(self.source), before)
        self.assertEqual(restored.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backup.parent.stat().st_mode & 0o777, 0o700)
        with StateStore(restored) as store:
            self.assertEqual(len(store.list_audit('test')), 2)
            self.assertEqual(len(store.pending_intents('test')), 1)
            self.assertEqual(store.pending_intents('test')[0]['attempts'], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                store.connection.execute('DELETE FROM audit_entries')

    def test_active_worker_lock_refuses_backup_without_publishing(self):
        with StateStore(self.source), self.assertRaisesRegex(SnapshotError, 'snapshot_source_in_use'):
            snapshot(self.source, self.backup)
        self.assertFalse(self.backup.exists())
        snapshot(self.source, self.backup)

    def test_existing_destination_and_same_source_never_overwritten(self):
        snapshot(self.source, self.backup)
        before = self.backup.read_bytes()
        for target in (self.source, self.backup):
            with self.assertRaisesRegex(SnapshotError, 'snapshot_destination_exists'):
                snapshot(self.source, target)
        self.assertEqual(self.backup.read_bytes(), before)

    def test_symlink_relative_missing_and_private_permissions_fail_closed(self):
        link = self.root/'symlink'
        link.symlink_to(self.source)
        for source, target, reason in ((link, self.backup, 'symlink'),
            (self.source, link, 'symlink'), (Path('relative'), self.backup, 'absolute'),
            (self.root/'absent', self.backup, 'source_missing')):
            with self.assertRaisesRegex(SnapshotError, reason):
                snapshot(source, target)
        self.source.chmod(0o644)
        with self.assertRaisesRegex(SnapshotError, 'private_owned_file'):
            snapshot(self.source, self.backup)
        self.source.chmod(0o600)
        with tempfile.NamedTemporaryFile(dir='/tmp', suffix='.sqlite3', delete=False) as stream:
            stream.write(self.source.read_bytes())
            shared_source = Path(stream.name)
        try:
            with self.assertRaisesRegex(SnapshotError, 'snapshot_untrusted_directory'):
                snapshot(shared_source, self.backup)
        finally:
            shared_source.unlink(missing_ok=True)
        self.backup.parent.mkdir(mode=0o755)
        self.backup.parent.chmod(0o755)
        with self.assertRaisesRegex(SnapshotError, 'private_destination_directory'):
            snapshot(self.source, self.backup)

    def test_sqlite_sidecar_destination_refused(self):
        for suffix in ('.lock', '-wal', '-shm', '-journal'):
            with self.assertRaises(SnapshotError):
                snapshot(self.source, Path(str(self.source)+suffix))
        self.assertEqual(list(self.root.glob('.snapshot-*')), [])

    def test_publication_race_does_not_overwrite_other_file(self):
        def race(source, destination):
            destination.write_bytes(b'OTHER')
            raise FileExistsError()
        with patch('scripts.state_snapshot.os.link', side_effect=race):
            with self.assertRaisesRegex(SnapshotError, 'destination_exists'):
                snapshot(self.source, self.backup)
        self.assertEqual(self.backup.read_bytes(), b'OTHER')
        self.assertEqual(list(self.backup.parent.glob('.snapshot-*')), [])
        original_link = os.link
        replacement = self.root/'replacement.sqlite3'
        replacement.write_bytes(self.source.read_bytes())
        replacement.chmod(0o600)
        def replace_published(source, destination):
            original_link(source, destination)
            os.replace(replacement, destination)
        with patch('scripts.state_snapshot.os.link', side_effect=replace_published):
            with self.assertRaisesRegex(SnapshotError, 'snapshot_destination_changed'):
                snapshot(self.source, self.root/'published.sqlite3')

    def test_failed_copy_is_not_published_and_partial_file_cleaned(self):
        import scripts.state_snapshot as module
        original = module._inspect
        count = 0
        def fail_output(connection):
            nonlocal count
            count += 1
            if count == 2:
                raise SnapshotError('snapshot_verification_failed')
            return original(connection)
        with patch.object(module, '_inspect', side_effect=fail_output):
            with self.assertRaisesRegex(SnapshotError, 'verification_failed'):
                snapshot(self.source, self.backup)
        self.assertFalse(self.backup.exists())
        self.assertEqual(list(self.backup.parent.glob('.snapshot-*')), [])

    def test_future_schema_and_foreign_key_damage_are_rejected(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute('PRAGMA user_version=999')
        with self.assertRaisesRegex(SnapshotError, 'schema_unsupported'):
            snapshot(self.source, self.backup)
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute('PRAGMA user_version=2')
            connection.execute("DELETE FROM runs WHERE purpose='manual'")
            connection.commit()
        with self.assertRaisesRegex(SnapshotError, 'foreign_keys_failed'):
            snapshot(self.source, self.root/'fk-rejected.sqlite3')
        self.assertFalse(self.backup.exists())

    def test_destination_orphan_sidecars_and_reservation_races_are_refused(self):
        self.backup.parent.mkdir(mode=0o700)
        for suffix in ('.lock', '-wal', '-shm', '-journal'):
            sidecar = Path(str(self.backup)+suffix)
            sidecar.write_bytes(b'orphan')
            with self.assertRaisesRegex(SnapshotError, 'destination_sidecars_present'):
                snapshot(self.source, self.backup)
            self.assertFalse(self.backup.exists())
            sidecar.unlink()
        original = sqlite3.connect
        def add_orphan(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] == self.source.as_uri()+'?mode=rw':
                Path(str(self.backup)+'-journal').write_bytes(b'orphan')
            return result
        with patch('scripts.state_snapshot.sqlite3.connect', side_effect=add_orphan):
            with self.assertRaisesRegex(SnapshotError, 'destination_sidecars_present'):
                snapshot(self.source, self.backup)
        self.assertFalse(self.backup.exists())

    def test_source_and_source_lock_replacement_are_detected(self):
        baseline = self.source.read_bytes()
        for variant in ('source-lock', 'source-file', 'sqlite-open'):
            case = self.root/variant
            case.mkdir(mode=0o700)
            source = case/'state.sqlite3'
            source.write_bytes(baseline)
            source.chmod(0o600)
            other = case/'other.sqlite3'
            other.write_bytes(baseline)
            other.chmod(0o600)
            target = case/'backup.sqlite3'
            original_flock, original_connect = fcntl.flock, sqlite3.connect
            mutated = False
            def swap(fd, flags):
                nonlocal mutated
                original_flock(fd, flags)
                if not mutated:
                    mutated = True
                    if variant == 'source-lock':
                        path = Path(str(source)+'.lock')
                        path.unlink()
                        path.write_bytes(b'replacement')
                        path.chmod(0o600)
                    elif variant == 'source-file':
                        source.unlink()
                        source.symlink_to(other)
            def replace_at_open(*args, **kwargs):
                if args[0] == source.as_uri()+'?mode=rw':
                    os.replace(other, source)
                return original_connect(*args, **kwargs)
            with patch('scripts.state_snapshot.fcntl.flock', side_effect=swap), \
                 patch('scripts.state_snapshot.sqlite3.connect', side_effect=replace_at_open if variant=='sqlite-open' else original_connect):
                with self.assertRaisesRegex(SnapshotError, 'snapshot_source'):
                    snapshot(source, target)
            self.assertFalse(target.exists())

    def test_missing_protective_trigger_index_or_modified_table_refused(self):
        baseline = self.source.read_bytes()
        for index, statement in enumerate(('DROP TRIGGER audit_no_delete', 'DROP INDEX audit_by_configuration',
                                          'ALTER TABLE configurations ADD COLUMN unexpected TEXT')):
            source = self.root/f'tampered-{index}.sqlite3'
            source.write_bytes(baseline)
            source.chmod(0o600)
            with closing(sqlite3.connect(source)) as connection:
                connection.execute(statement)
            with self.assertRaisesRegex(SnapshotError, 'snapshot_schema_mismatch'):
                snapshot(source, self.root/f'rejected-{index}.sqlite3')

    def test_incomplete_database_schema_is_not_published(self):
        incomplete = self.root/'incomplete.sqlite3'
        with closing(sqlite3.connect(incomplete)) as connection:
            connection.execute('PRAGMA user_version=2')
        incomplete.chmod(0o600)
        with self.assertRaisesRegex(SnapshotError, 'schema_incomplete'):
            snapshot(incomplete, self.backup)
        self.assertFalse(self.backup.exists())

    def test_v1_snapshot_does_not_migrate_source_and_restore_upgrades_on_open(self):
        old = self.root/'v1.sqlite3'
        with patch('calendar_sync.state.MIGRATIONS', MIGRATIONS[:1]):
            with StateStore(old) as store:
                store.create_configuration('old', 'operator', 'google', 'url',
                    google_credential_file='/private/google', nextcloud_credential_file='/private/nextcloud')
        self.assertEqual(snapshot(old, self.backup), (1, 0))
        with closing(sqlite3.connect(old)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 1)
        restored = self.root/'restored-v1'/'state.sqlite3'
        snapshot(self.backup, restored)
        with StateStore(restored) as store:
            self.assertEqual(store.connection.execute('PRAGMA user_version').fetchone()[0], 2)
        with closing(sqlite3.connect(self.backup)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 1)

    def test_cli_reports_only_fixed_codes_and_aggregate_metadata(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(['backup', '--source', str(self.source), '--destination', str(self.backup)]), 0)
        self.assertIn('schema=2; mappings=1', output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())
        restored = self.root/'cli-restored'/'state.sqlite3'
        with redirect_stdout(output):
            self.assertEqual(main(['restore', '--source', str(self.backup), '--destination', str(restored)]), 0)
        self.assertEqual(self.contents(restored), self.contents(self.source))
        with patch('scripts.state_snapshot.snapshot', side_effect=OSError('SENTINEL_SECRET')), redirect_stdout(output):
            self.assertEqual(main(['restore', '--source', str(self.source), '--destination', str(self.backup)]), 2)
        self.assertNotIn('SENTINEL', output.getvalue())
