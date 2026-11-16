"""Credential-free acceptance exercise; only disposable local synthetic state."""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile

from calendar_sync.control import ControlError, ControlStore, OwnerContext, create_key
from calendar_sync.control_migration import import_legacy, restore_bundle, snapshot_bundle
from calendar_sync.state import StateStore


def expect(code, operation):
    try:
        operation()
    except ControlError as error:
        assert str(error)==code, 'unexpected fixed failure code'
    else:
        raise AssertionError('expected refusal')


def dump(path):
    with closing(sqlite3.connect(path)) as connection:
        return '\n'.join(connection.iterdump())


def main():
    with tempfile.TemporaryDirectory(prefix='calendar-control-uat-') as directory:
        root = Path(directory)
        key = root/'keys'/'vault.key'
        new_key = root/'keys'/'rotated.key'
        database = root/'control'/'control.sqlite3'
        engine_file = root/'engine'/'engine.sqlite3'
        create_key(key)
        create_key(new_key)
        alice = OwnerContext('https://example.invalid/realm','synthetic-alice')
        bob = OwnerContext('https://example.invalid/realm','synthetic-bob')
        with StateStore(engine_file) as engine:
            for name in ('legacy','unclaimed'):
                engine.create_configuration(name,'operator','synthetic-'+name,
                    'https://example.invalid/calendars/'+name+'/',
                    google_credential_file='/synthetic/not-read/google',
                    nextcloud_credential_file='/synthetic/not-read/nextcloud')
            run = engine.start_run('legacy',dry_run=False,purpose='execution')
            engine.record_applied('legacy',run,source_event_id='fixture',destination_href='fixture.ics',
                destination_uid='fixture-uid',source_fingerprint='fixture-fingerprint',destination_etag='"fixture"',action='create')
        original = dump(engine_file)
        with ControlStore(database,key,maintenance=True) as store:
            owner = store.register_owner(alice)
            store.register_owner(bob)
            print('PASS CTRL-UAT-01: private key/control store and explicit synthetic owners')
            google = store.create_connection(alice,'google','synthetic-account',b'SYNTHETIC_REFRESH_NOT_REAL')
            nextcloud = store.create_connection(alice,'nextcloud','synthetic-account',b'SYNTHETIC_PASSWORD_NOT_REAL')
            expect('control_not_found',lambda:store.resolve_credentials(bob,google))
            assert 'SYNTHETIC_REFRESH_NOT_REAL' not in dump(database)
            assert store.list_connections(bob)==[]
            print('PASS CTRL-UAT-02: owner denial and encrypted/redacted credentials')
            pair = store.create_pair(alice,google,nextcloud,('google:disjoint','nextcloud:disjoint'))
            assert store.get_pair(alice,pair)['dry_run']==1
            assert store.get_pair(alice,pair)['state']=='paused'
            store.archive_pair(alice,pair,1)
            expect('control_endpoint_unavailable',lambda:store.create_pair(
                alice,google,nextcloud,('google:disjoint','nextcloud:another')))
            print('PASS CTRL-UAT-03: paused/dry-run defaults and retained endpoint reservations')
            before = dump(database)
            assert import_legacy(store,engine_file,{'legacy':owner})['applied'] is False
            assert dump(database)==before
            import_legacy(store,engine_file,{'legacy':owner},apply=True)
            after = dump(database)
            import_legacy(store,engine_file,{'legacy':owner},apply=True)
            assert dump(database)==after
            assert dump(engine_file)==original
            assert store.connection.execute("SELECT status FROM legacy_imports WHERE configuration_id='unclaimed'").fetchone()[0]=='quarantined'
            print('PASS CTRL-UAT-04: preview rollback, idempotent import, quarantine and unchanged engine')
            snapshot_bundle(store,engine_file,root/'bundle')
            store.rotate_key(new_key)
            assert store.resolve_credentials(alice,google)==b'SYNTHETIC_REFRESH_NOT_REAL'
            print('PASS CTRL-UAT-05: quiesced bundle and atomic key rotation')
        def open_old():
            with ControlStore(database,key):
                pass
        expect('control_vault_authentication_failed',open_old)
        expect('control_vault_authentication_failed',lambda:restore_bundle(root/'bundle',root/'wrong-key',new_key))
        restore_bundle(root/'bundle',root/'isolated',key)
        with ControlStore(root/'isolated'/'control.sqlite3',key) as restored:
            assert restored.resolve_credentials(alice,google)==b'SYNTHETIC_REFRESH_NOT_REAL'
        assert dump(root/'isolated'/'engine.sqlite3')==original
        assert dump(engine_file)==original
        print('PASS CTRL-UAT-06: correct-key isolated restore; wrong key refused; source retained')
    print('PASS: synthetic files cleaned; no network, real credentials or provider writes')


if __name__=='__main__':
    main()
