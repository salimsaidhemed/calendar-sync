from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from calendar_sync.control import ControlError, ControlStore, OwnerContext, create_key
from calendar_sync.control_admin import main, read_assignments
from calendar_sync.control_migration import import_legacy, restore_bundle, snapshot_bundle
from calendar_sync.state import StateStore


class ControlMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.key = self.root/'keys'/'key'
        self.database = self.root/'control'/'control.sqlite3'
        self.engine = self.root/'engine'/'engine.sqlite3'
        create_key(self.key)
        self.alice = OwnerContext('https://issuer.invalid','alice')
        self.bob = OwnerContext('https://issuer.invalid','bob')
        with StateStore(self.engine) as engine:
            for name in ('legacy','unassigned'):
                engine.create_configuration(name,'operator','google-'+name,
                    'https://nextcloud.invalid/remote.php/dav/calendars/user/'+name+'/',
                    google_credential_file='/never-read/GOOGLE_SENTINEL',
                    nextcloud_credential_file='/never-read/NEXTCLOUD_SENTINEL')
            run = engine.start_run('legacy',dry_run=False,purpose='execution')
            engine.record_applied('legacy',run,source_event_id='existing',destination_href='old.ics',
                destination_uid='managed-uid',source_fingerprint='fingerprint',destination_etag='"etag"',action='create')
            engine.prepare_intent('legacy',run,dict(id='pending',source_event_id='pending-source',action='create',
                destination_href='pending.ics',destination_uid='pending-uid',source_fingerprint='a'*64,
                owner_marker='b'*64,payload_hash='c'*64,stamp='2026-11-16T00:00:00+00:00',
                expected_etag=None,baseline_fingerprint=None))
        with self.store() as store:
            self.owner = store.register_owner(self.alice)
            self.other_owner = store.register_owner(self.bob)

    def store(self, maintenance=True):
        return ControlStore(self.database,self.key,maintenance=maintenance)

    def dump(self, path):
        with closing(sqlite3.connect(path)) as connection:
            return '\n'.join(connection.iterdump())

    def test_import_preview_rolls_back_and_apply_preserves_engine_bytes_mappings_intents_and_markers(self):
        before_engine = self.engine.read_bytes()
        with self.store() as store:
            before = self.dump(self.database)
            report = import_legacy(store,self.engine,{'legacy':self.owner})
            self.assertEqual(report,dict(configurations=2,assigned=1,quarantined=1,applied=False))
            self.assertEqual(self.dump(self.database),before)
            self.assertTrue(import_legacy(store,self.engine,{'legacy':self.owner},apply=True)['applied'])
            pair = store.connection.execute("SELECT * FROM pairs WHERE engine_configuration_id='legacy'").fetchone()
            self.assertEqual((pair['state'],pair['dry_run'],pair['legacy_review']),('paused',1,1))
            self.assertEqual(store.connection.execute("SELECT status FROM legacy_imports WHERE configuration_id='unassigned'")
                             .fetchone()[0],'quarantined')
            self.assertEqual(store.list_connections(self.bob),[])
            for connection in store.list_connections(self.alice):
                with self.assertRaisesRegex(ControlError,'control_connection_unavailable'):
                    store.resolve_credentials(self.alice,connection['id'])
            self.assertNotIn('GOOGLE_SENTINEL',self.dump(self.database))
        self.assertEqual(self.engine.read_bytes(),before_engine)
        with StateStore(self.engine) as engine:
            self.assertEqual(engine.get_mapping('legacy','existing')['destination_uid'],'managed-uid')
            self.assertEqual(engine.get_intent('legacy','pending')['owner_marker'],'b'*64)
            self.assertEqual(engine.connection.execute("SELECT owner_subject FROM configurations WHERE id='legacy'")
                             .fetchone()[0],'operator')

    def test_import_retry_is_idempotent_and_quarantine_assignment_is_explicit(self):
        with self.store() as store:
            import_legacy(store,self.engine,{'legacy':self.owner},apply=True)
            before = self.dump(self.database)
            import_legacy(store,self.engine,{'legacy':self.owner},apply=True)
            self.assertEqual(self.dump(self.database),before)
            google = store.create_connection(self.bob,'google','new-account',b'x')
            nc = store.create_connection(self.bob,'nextcloud','new-account',b'x')
            with self.assertRaisesRegex(ControlError,'control_endpoint_unavailable'):
                store.create_pair(self.bob,google,nc,('google:google-unassigned','nextcloud:unrelated'))
            import_legacy(store,self.engine,{'legacy':self.owner,'unassigned':self.other_owner},apply=True)
            self.assertEqual(len(store.list_connections(self.bob)),4)
            self.assertEqual(store.connection.execute('SELECT count(*) FROM pairs').fetchone()[0],2)
            self.assertEqual(store.connection.execute('SELECT count(*) FROM quarantine_reservations').fetchone()[0],0)

    def test_unknown_disabled_or_reassigned_owner_and_unknown_configuration_are_refused_atomically(self):
        with self.store() as store:
            for manifest in ({'missing':self.owner},{'legacy':'missing-owner'}):
                before = self.dump(self.database)
                with self.assertRaises(ControlError):
                    import_legacy(store,self.engine,manifest,apply=True)
                self.assertEqual(self.dump(self.database),before)
            import_legacy(store,self.engine,{'legacy':self.owner},apply=True)
            for manifest in ({'legacy':self.other_owner},{}, {'legacy':None}):
                before = self.dump(self.database)
                with self.assertRaisesRegex(ControlError,'control_legacy_reassignment_refused'):
                    import_legacy(store,self.engine,manifest,apply=True)
                self.assertEqual(self.dump(self.database),before)
            store.disable_owner(self.bob)
            with self.assertRaisesRegex(ControlError,'control_manifest_owner_unavailable'):
                import_legacy(store,self.engine,{'legacy':self.owner,'unassigned':self.other_owner},apply=True)

    def test_failed_partial_import_rolls_back_connections_pairs_reservations_and_audit(self):
        with self.store() as store:
            before = self.dump(self.database)
            original = store._insert_pair
            count = 0
            def fail(*args,**kwargs):
                nonlocal count
                count+=1
                if count==2:
                    raise ControlError('control_test_import_failure')
                return original(*args,**kwargs)
            with patch.object(store,'_insert_pair',side_effect=fail), self.assertRaisesRegex(ControlError,'control_test_import_failure'):
                import_legacy(store,self.engine,{'legacy':self.owner,'unassigned':self.other_owner},apply=True)
            self.assertEqual(self.dump(self.database),before)

    def test_active_engine_and_nonmaintenance_control_store_refuse_import_or_snapshot(self):
        with self.store(maintenance=False) as store:
            with self.assertRaisesRegex(ControlError,'control_maintenance_required'):
                import_legacy(store,self.engine,{})
            with self.assertRaisesRegex(ControlError,'control_maintenance_required'):
                snapshot_bundle(store,self.engine,self.root/'bundle')
        with self.store() as store, StateStore(self.engine):
            for operation in (lambda:import_legacy(store,self.engine,{}),
                              lambda:snapshot_bundle(store,self.engine,self.root/'bundle')):
                with self.assertRaisesRegex(ControlError,'control_engine_locked'):
                    operation()

    def test_engine_changes_or_wrong_legacy_owner_block_reimport_and_bundle(self):
        with self.store() as store:
            import_legacy(store,self.engine,{'legacy':self.owner},apply=True)
            with closing(sqlite3.connect(self.engine)) as engine, engine:
                engine.execute("UPDATE configurations SET google_calendar_id='changed' WHERE id='legacy'")
            for operation in (lambda:import_legacy(store,self.engine,{'legacy':self.owner}),
                              lambda:snapshot_bundle(store,self.engine,self.root/'bundle')):
                with self.assertRaisesRegex(ControlError,'control_engine_changed_requires_review'):
                    operation()
            with closing(sqlite3.connect(self.engine)) as engine, engine:
                engine.execute("UPDATE configurations SET owner_subject='other-owner' WHERE id='legacy'")
            with self.assertRaisesRegex(ControlError,'control_legacy_owner_requires_review'):
                import_legacy(store,self.engine,{'legacy':self.owner})

    def test_bundle_roundtrip_recovers_both_databases_and_credentials_without_key_copy(self):
        bundle = self.root/'bundle'
        restored = self.root/'restored'
        with self.store() as store:
            connection = store.create_connection(self.alice,'google','verified-account',b'SENTINEL_REFRESH')
            import_legacy(store,self.engine,{'legacy':self.owner},apply=True)
            snapshot_bundle(store,self.engine,bundle)
            control_dump = self.dump(self.database)
        self.assertEqual({p.name for p in bundle.iterdir()},{'control.sqlite3','engine.sqlite3','manifest.enc'})
        self.assertNotIn(b'SENTINEL_REFRESH',(bundle/'control.sqlite3').read_bytes())
        self.assertEqual(restore_bundle(bundle,restored,self.key),dict(control_schema=1,engine_schema=2))
        self.assertEqual(self.dump(restored/'control.sqlite3'),control_dump)
        self.assertEqual(self.dump(restored/'engine.sqlite3'),self.dump(self.engine))
        with ControlStore(restored/'control.sqlite3',self.key) as store:
            self.assertEqual(store.resolve_credentials(self.alice,connection),b'SENTINEL_REFRESH')

    def test_bundle_tamper_wrong_key_existing_destination_and_failure_leave_no_partial_restore(self):
        bundle = self.root/'bundle'
        with self.store() as store:
            snapshot_bundle(store,self.engine,bundle)
            with self.assertRaisesRegex(ControlError,'control_bundle_creation_failed'):
                snapshot_bundle(store,self.engine,bundle)
        other = self.root/'keys'/'other'
        create_key(other)
        with self.assertRaisesRegex(ControlError,'control_vault_authentication_failed'):
            restore_bundle(bundle,self.root/'wrong-key',other)
        self.assertFalse((self.root/'wrong-key').exists())
        restore_bundle(bundle,self.root/'existing',self.key)
        before = self.dump(self.root/'existing'/'control.sqlite3')
        with self.assertRaisesRegex(ControlError,'control_bundle_restore_failed'):
            restore_bundle(bundle,self.root/'existing',self.key)
        self.assertEqual(self.dump(self.root/'existing'/'control.sqlite3'),before)
        with (bundle/'engine.sqlite3').open('ab') as stream:
            stream.write(b'corrupt')
        with self.assertRaisesRegex(ControlError,'control_bundle_hash_mismatch'):
            restore_bundle(bundle,self.root/'tampered',self.key)
        self.assertFalse((self.root/'tampered').exists())

    def test_unsupported_engine_schema_sidecars_and_missing_database_are_not_mutated(self):
        with self.store() as store:
            for marker in ('-wal','-shm','-journal'):
                path = Path(str(self.engine)+marker)
                path.write_bytes(b'sidecar')
                try:
                    with self.assertRaisesRegex(ControlError,'control_engine_sidecar_requires_recovery'):
                        import_legacy(store,self.engine,{})
                finally:
                    path.unlink()
            with closing(sqlite3.connect(self.engine)) as engine, engine:
                engine.execute('PRAGMA user_version=999')
            before = self.engine.read_bytes()
            with self.assertRaisesRegex(ControlError,'control_engine_schema_unsupported'):
                import_legacy(store,self.engine,{})
            self.assertEqual(self.engine.read_bytes(),before)
            with self.assertRaisesRegex(ControlError,'control_engine_read_failed'):
                import_legacy(store,self.root/'missing',{})
            self.assertFalse((self.root/'missing').exists())

    def test_duplicate_manifest_keys_and_cli_preview_apply_are_explicit_and_secret_free(self):
        manifest = self.root/'assignments.json'
        manifest.write_text('{"legacy":"one","legacy":"two"}')
        manifest.chmod(0o600)
        with self.assertRaisesRegex(ControlError,'control_manifest_duplicate'):
            read_assignments(manifest)
        manifest.write_text(json.dumps({'legacy':self.owner}))
        args = ['--key-file',str(self.key),'--database',str(self.database),'import-legacy',
                '--engine-file',str(self.engine),'--assignments',str(manifest)]
        for extra,applied in (([],False),(['--apply'],True)):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(args+extra),0)
            self.assertEqual(json.loads(output.getvalue())['applied'],applied)
            self.assertNotIn('SENTINEL',output.getvalue())

    def test_restore_failure_after_copy_cleans_only_new_destination_and_keeps_bundle(self):
        bundle = self.root/'bundle'
        target = self.root/'restored'
        with self.store() as store:
            snapshot_bundle(store,self.engine,bundle)
        with patch('calendar_sync.control_migration.ControlStore',side_effect=ControlError('control_test_failure')):
            with self.assertRaisesRegex(ControlError,'control_test_failure'):
                restore_bundle(bundle,target,self.key)
        self.assertFalse(target.exists())
        self.assertTrue((bundle/'manifest.enc').exists())

    def test_old_backup_requires_old_key_after_rotation_and_restores_only_to_isolated_state(self):
        bundle = self.root/'before-rotation'
        new_key = self.root/'keys'/'rotated'
        create_key(new_key)
        with self.store() as store:
            connection = store.create_connection(self.alice,'google','account',b'old-backup-secret')
            snapshot_bundle(store,self.engine,bundle)
            store.rotate_key(new_key)
        with self.assertRaisesRegex(ControlError,'control_vault_authentication_failed'):
            restore_bundle(bundle,self.root/'wrong',new_key)
        restore_bundle(bundle,self.root/'isolated',self.key)
        with ControlStore(self.root/'isolated'/'control.sqlite3',self.key) as store:
            self.assertEqual(store.resolve_credentials(self.alice,connection),b'old-backup-secret')
        with ControlStore(self.database,new_key) as store:
            self.assertEqual(store.resolve_credentials(self.alice,connection),b'old-backup-secret')
