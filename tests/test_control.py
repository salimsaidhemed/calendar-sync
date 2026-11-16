import io
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from unittest.mock import patch

from calendar_sync.control import ControlError, ControlStore, OwnerContext, Vault, create_key
from calendar_sync.control_admin import main


def race_pair(database,key,google,nextcloud,barrier,results):
    try:
        with ControlStore(database,key) as store:
            barrier.wait(timeout=10)
            store.create_pair(OwnerContext('https://issuer.invalid','alice'),google,nextcloud,
                              ('google:shared','nextcloud:shared'))
            results.put('created')
    except ControlError as error:
        results.put(str(error))


class ControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.key = self.root/'keys'/'vault.key'
        self.database = self.root/'control'/'control.sqlite3'
        create_key(self.key)
        self.alice = OwnerContext('https://issuer.invalid','alice')
        self.bob = OwnerContext('https://issuer.invalid','bob')

    def store(self, **kwargs):
        return ControlStore(self.database,self.key,**kwargs)

    def setup_connections(self, store, context=None):
        context = context or self.alice
        store.register_owner(context)
        return (store.create_connection(context,'google','account',b'SENTINEL_GOOGLE_REFRESH'),
                store.create_connection(context,'nextcloud','nextcloud-account',b'SENTINEL_NC_PASSWORD'))

    def test_identity_is_exact_issuer_subject_and_provisioning_is_idempotent(self):
        with self.store() as store:
            first = store.register_owner(self.alice)
            self.assertEqual(store.register_owner(self.alice),first)
            self.assertNotEqual(store.register_owner(OwnerContext('https://other.invalid','alice')),first)
            with self.assertRaisesRegex(ControlError,'control_owner_required'):
                store.register_owner({'issuer':'attacker'})
            with self.assertRaisesRegex(ControlError,'control_owner_unavailable'):
                store.list_connections(self.bob)

    def test_credentials_are_encrypted_redacted_and_bound_to_connection_owner_provider_revision(self):
        with self.store() as store:
            google,nextcloud = self.setup_connections(store)
            self.assertEqual(store.resolve_credentials(self.alice,google),b'SENTINEL_GOOGLE_REFRESH')
            self.assertNotIn('envelope',repr(store.list_connections(self.alice)))
            self.assertNotIn('SENTINEL', '\n'.join(store.connection.iterdump()))
            row = store.connection.execute('SELECT envelope FROM connections WHERE id=?',(google,)).fetchone()
            for field,value in (('owner_id',store.register_owner(self.bob)),('provider','nextcloud'),('revision',7)):
                with self.subTest(field=field):
                    with store.transaction():
                        original = store.connection.execute('SELECT '+field+' FROM connections WHERE id=?',(google,)).fetchone()[0]
                        store.connection.execute('UPDATE connections SET '+field+'=? WHERE id=?',(value,google))
                    context = self.bob if field=='owner_id' else self.alice
                    with self.assertRaisesRegex(ControlError,'control_vault_authentication_failed'):
                        store.resolve_credentials(context,google)
                    with store.transaction():
                        store.connection.execute('UPDATE connections SET '+field+'=? WHERE id=?',(original,google))
            with store.transaction():
                store.connection.execute('UPDATE connections SET envelope=? WHERE id=?',(row[0],nextcloud))
            with self.assertRaisesRegex(ControlError,'control_vault_authentication_failed'):
                store.resolve_credentials(self.alice,nextcloud)
        self.assertNotIn(b'SENTINEL_GOOGLE_REFRESH',self.database.read_bytes())

    def test_all_owner_scoped_operations_deny_foreign_connections_pairs_audit_and_secrets(self):
        with self.store() as store:
            google,nextcloud = self.setup_connections(store)
            store.register_owner(self.bob)
            pair = store.create_pair(self.alice,google,nextcloud,('google:a','nextcloud:a'))
            self.assertEqual(store.list_connections(self.bob),[])
            for operation in (lambda:store.resolve_credentials(self.bob,google),
                              lambda:store.replace_credentials(self.bob,google,1,b'x'),
                              lambda:store.disconnect(self.bob,google),
                              lambda:store.get_pair(self.bob,pair),
                              lambda:store.archive_pair(self.bob,pair,1),
                              lambda:store.create_pair(self.bob,google,nextcloud,('google:b','nextcloud:b'))):
                with self.assertRaisesRegex(ControlError,'^control_not_found$'):
                    operation()
            self.assertEqual([r['action'] for r in store.list_audit(self.bob)],['owner_created'])
            with self.assertRaisesRegex(ControlError,'^control_not_found$'):
                store.get_pair(self.bob,'unknown')

    def test_credential_refresh_compare_and_swap_rejects_stale_revision_and_invalid_payload(self):
        with self.store() as store:
            google,_ = self.setup_connections(store)
            self.assertEqual(store.replace_credentials(self.alice,google,1,b'new'),2)
            with self.assertRaisesRegex(ControlError,'control_revision_changed'):
                store.replace_credentials(self.alice,google,1,b'old')
            for revision,payload in ((True,b'x'),(0,b'x'),(2,b''),(2,b'x'*65537)):
                with self.assertRaises(ControlError):
                    store.replace_credentials(self.alice,google,revision,payload)
            self.assertEqual(store.resolve_credentials(self.alice,google),b'new')

    def test_disconnect_and_disabled_owner_block_credential_use_and_new_pairs(self):
        with self.store() as store:
            google,nextcloud = self.setup_connections(store)
            store.disconnect(self.alice,google)
            for operation in (lambda:store.resolve_credentials(self.alice,google),
                              lambda:store.replace_credentials(self.alice,google,1,b'new'),
                              lambda:store.create_pair(self.alice,google,nextcloud,('google:a','nextcloud:a'))):
                with self.assertRaisesRegex(ControlError,'control_connection_unavailable'):
                    operation()
            store.disable_owner(self.alice)
            with self.assertRaisesRegex(ControlError,'control_owner_unavailable'):
                store.list_connections(self.alice)

    def test_pair_defaults_archive_and_global_reservations_preserve_history(self):
        with self.store() as store:
            google,nextcloud = self.setup_connections(store)
            pair = store.create_pair(self.alice,google,nextcloud,('google:a','nextcloud:a'))
            self.assertEqual(store.get_pair(self.alice,pair),dict(id=pair,revision=1,state='paused',
                dry_run=1,direction='google_to_nextcloud',legacy_review=0))
            store.archive_pair(self.alice,pair,1)
            self.assertEqual(store.get_pair(self.alice,pair)['revision'],2)
            with self.assertRaisesRegex(ControlError,'control_revision_changed'):
                store.archive_pair(self.alice,pair,1)
            other_google,other_nc = self.setup_connections(store,self.bob)
            before = '\n'.join(store.connection.iterdump())
            # Different viewing connections, same canonical endpoint remains reserved.
            for endpoints in (('google:a','nextcloud:b'),('google:b','nextcloud:a')):
                with self.assertRaisesRegex(ControlError,'control_endpoint_unavailable'):
                    store.create_pair(self.bob,other_google,other_nc,endpoints)
                self.assertEqual('\n'.join(store.connection.iterdump()),before)

    def test_concurrent_normal_processes_reserve_both_endpoints_atomically(self):
        with self.store() as store:
            google,nextcloud = self.setup_connections(store)
        context = multiprocessing.get_context('spawn')
        barrier = context.Barrier(2)
        results = context.Queue()
        workers = [context.Process(target=race_pair,args=(self.database,self.key,google,nextcloud,barrier,results))
                   for _ in range(2)]
        try:
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=20)
                self.assertFalse(worker.is_alive())
                self.assertEqual(worker.exitcode,0)
            self.assertCountEqual([results.get(timeout=5) for _ in workers],['created','control_endpoint_unavailable'])
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join()
            results.close()
            results.join_thread()
        with self.store() as store:
            self.assertEqual(store.connection.execute('SELECT count(*) FROM pairs').fetchone()[0],1)
            self.assertEqual(store.connection.execute('SELECT count(*) FROM reservations').fetchone()[0],2)

    def test_unknown_schema_and_schema_drift_refuse_without_mutating_database(self):
        with self.store():
            pass
        for statement,code in (('PRAGMA user_version=999','control_schema_unsupported'),
                               ('CREATE TABLE unexpected(secret TEXT)','control_schema_mismatch')):
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute('PRAGMA user_version=1')
                connection.execute(statement)
            before = self.database.read_bytes()
            with self.assertRaisesRegex(ControlError,code):
                with self.store():
                    pass
            self.assertEqual(self.database.read_bytes(),before)

    def test_key_files_are_private_exact_length_never_overwritten_and_separate(self):
        original = self.key.read_bytes()
        with self.assertRaisesRegex(ControlError,'control_key_creation_failed'):
            create_key(self.key)
        self.assertEqual(self.key.read_bytes(),original)
        with self.assertRaisesRegex(ControlError,'control_key_must_be_separate'):
            ControlStore(self.key.parent/'control.sqlite3',self.key)
        for value in (b'',b'x'*31,b'x'*33):
            self.key.write_bytes(value)
            with self.assertRaisesRegex(ControlError,'control_key_invalid'):
                Vault(self.key)
        self.key.write_bytes(original)
        self.key.chmod(0o644)
        with self.assertRaisesRegex(ControlError,'control_private_file_required'):
            Vault(self.key)

    def test_symlinks_hardlinks_fifo_and_broad_directories_are_rejected(self):
        alias = self.key.parent/'alias'
        alias.symlink_to(self.key)
        with self.assertRaisesRegex(ControlError,'control_unsafe_path'):
            Vault(alias)
        alias.unlink()
        os.link(self.key,alias)
        with self.assertRaisesRegex(ControlError,'control_private_file_required'):
            Vault(self.key)
        alias.unlink()
        os.mkfifo(alias,0o600)
        with self.assertRaisesRegex(ControlError,'control_private_file_required'):
            Vault(alias)
        self.key.parent.chmod(0o755)
        with self.assertRaisesRegex(ControlError,'control_private_directory_required'):
            Vault(self.key)

    def test_wrong_or_missing_key_and_modified_ciphertext_fail_closed(self):
        with self.store() as store:
            google,_ = self.setup_connections(store)
            with store.transaction():
                store.connection.execute('UPDATE connections SET envelope=? WHERE id=?',(b'tampered',google))
            with self.assertRaisesRegex(ControlError,'control_vault_authentication_failed'):
                store.resolve_credentials(self.alice,google)
        other = self.root/'keys'/'other'
        create_key(other)
        for key,code in ((other,'control_vault_authentication_failed'),(self.root/'missing','control_key_unavailable')):
            with self.assertRaisesRegex(ControlError,code):
                with ControlStore(self.database,key):
                    pass

    def test_maintenance_excludes_all_other_handles_and_normal_handles_coexist(self):
        with self.store():
            with self.store():
                pass
            with self.assertRaisesRegex(ControlError,'control_maintenance_locked'):
                with self.store(maintenance=True):
                    pass
        with self.store(maintenance=True):
            with self.assertRaisesRegex(ControlError,'control_maintenance_locked'):
                with self.store():
                    pass

    def test_key_rotation_preserves_payloads_requires_maintenance_and_changes_recovery_key(self):
        other = self.root/'keys'/'new'
        create_key(other)
        with self.store() as store:
            google,_ = self.setup_connections(store)
            with self.assertRaisesRegex(ControlError,'control_maintenance_required'):
                store.rotate_key(other)
        with self.store(maintenance=True) as store:
            store.rotate_key(other)
            self.assertEqual(store.resolve_credentials(self.alice,google),b'SENTINEL_GOOGLE_REFRESH')
        with self.assertRaisesRegex(ControlError,'control_vault_authentication_failed'):
            with self.store():
                pass
        with ControlStore(self.database,other) as store:
            self.assertEqual(store.resolve_credentials(self.alice,google),b'SENTINEL_GOOGLE_REFRESH')

    def test_failed_key_rotation_rolls_back_every_ciphertext_and_audit_entry(self):
        other = self.root/'keys'/'new'
        create_key(other)
        with self.store(maintenance=True) as store:
            self.setup_connections(store)
            before = '\n'.join(store.connection.iterdump())
            original = Vault.seal
            count = 0
            def fail(vault,payload,associated):
                nonlocal count
                count+=1
                if count==2:
                    raise ControlError('control_test_failure')
                return original(vault,payload,associated)
            with patch.object(Vault,'seal',fail), self.assertRaisesRegex(ControlError,'control_test_failure'):
                store.rotate_key(other)
            self.assertEqual('\n'.join(store.connection.iterdump()),before)

    def test_audit_is_owner_scoped_bounded_append_only_and_redacted(self):
        with self.store() as store:
            self.setup_connections(store)
            first = store.list_audit(self.alice,limit=1)
            rest = store.list_audit(self.alice,after_id=first[0]['id'])
            self.assertEqual(len(first)+len(rest),3)
            self.assertNotIn('SENTINEL',repr(first+rest))
            for limit in (0,101,True):
                with self.assertRaises(ControlError):
                    store.list_audit(self.alice,limit=limit)
            for sql in ('DELETE FROM security_audit','UPDATE security_audit SET action=action'):
                with self.assertRaises(sqlite3.IntegrityError):
                    store.connection.execute(sql)

    def test_operator_cli_prints_fixed_diagnostics_without_path_or_credential_contents(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(['--key-file',str(self.root/'SENTINEL_PRIVATE_MISSING'),
                           '--database',str(self.database),'init'])
        self.assertEqual(result,2)
        self.assertEqual(output.getvalue(),'control_key_unavailable\n')
        self.assertNotIn('SENTINEL',output.getvalue())
