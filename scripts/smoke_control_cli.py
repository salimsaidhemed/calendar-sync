"""Complete CTRL-UAT-07/08 using real CLI subprocesses and disposable fixtures.

No arguments, customer paths, credentials, Keycloak or network required. This
intentionally does not reuse .local/control-stage or any pre-existing files.
"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from calendar_sync.control import ControlStore, OwnerContext
from calendar_sync.state import StateStore


def dump(path):
    with closing(sqlite3.connect(path)) as connection:
        return '\n'.join(connection.iterdump())


def main():
    with tempfile.TemporaryDirectory(prefix='calendar-control-cli-uat-') as directory:
        root = Path(directory).resolve()
        key = root/'keys'/'old.key'
        new_key = root/'keys'/'new.key'
        database = root/'data'/'control.sqlite3'
        engine = root/'engine'/'engine.sqlite3'
        context = OwnerContext('https://synthetic.invalid/realm','synthetic-subject')

        def run(key_file,*arguments,database_required=True,failure=None):
            command = [sys.executable,'-m','calendar_sync.control_admin','--key-file',str(key_file)]
            if database_required:
                command.extend(('--database',str(database)))
            result = subprocess.run(command+list(arguments),capture_output=True,text=True,timeout=20)
            if failure:
                assert result.returncode==2 and result.stdout.strip()==failure and not result.stderr, 'CLI refusal differed'
                return None
            assert result.returncode==0 and not result.stderr, 'CLI operation failed'
            return json.loads(result.stdout)

        assert run(key,'init-key',database_required=False)=={'key_created':True}
        original_key = key.read_bytes()
        run(key,'init-key',database_required=False,failure='control_key_creation_failed')
        assert key.read_bytes()==original_key, 'existing key changed'
        assert run(key,'init')=={'control_schema':1}
        owner = run(key,'register-owner','--issuer',context.issuer,'--subject',context.subject)['owner_id']
        assert run(key,'register-owner','--issuer',context.issuer,'--subject',context.subject)['owner_id']==owner

        # Only the fixture constructor uses the library; all operator actions use CLI.
        with StateStore(engine) as store:
            for name in ('assigned-fixture','unassigned-fixture'):
                store.create_configuration(name,'operator','synthetic-'+name,
                    'https://synthetic.invalid/calendars/'+name+'/',
                    google_credential_file='/synthetic/never-read/google',
                    nextcloud_credential_file='/synthetic/never-read/nextcloud')
            run_id = store.start_run('assigned-fixture',dry_run=False,purpose='execution')
            store.record_applied('assigned-fixture',run_id,source_event_id='synthetic-event',
                destination_href='synthetic.ics',destination_uid='synthetic-uid',
                source_fingerprint='synthetic-fingerprint',destination_etag='"synthetic"',action='create')
        engine_before = engine.read_bytes()
        with ControlStore(database,key) as store:
            credential = store.create_connection(context,'google','synthetic-account',b'SYNTHETIC_NOT_A_TOKEN')
        assignments = root/'assignments.json'
        assignments.write_text(json.dumps({'assigned-fixture':owner}))
        assignments.chmod(0o600)
        arguments = ('import-legacy','--engine-file',str(engine),'--assignments',str(assignments))
        before = dump(database)
        assert run(key,*arguments)==dict(configurations=2,assigned=1,quarantined=1,applied=False)
        assert dump(database)==before, 'preview changed control records'
        assert run(key,*arguments,'--apply')['applied'] is True
        applied = dump(database)
        run(key,*arguments,'--apply')
        assert dump(database)==applied, 'retry changed imported state'
        assert engine.read_bytes()==engine_before, 'import changed engine'
        with ControlStore(database,key) as store:
            assert store.connection.execute("SELECT count(*) FROM legacy_imports WHERE status='quarantined'").fetchone()[0]==1
        print('PASS CTRL-UAT-07: CLI setup, owner storage, preview/apply/retry, quarantine and unchanged engine')

        bundle = root/'bundle'
        restored = root/'isolated-restore'
        run(key,'snapshot','--engine-file',str(engine),'--destination',str(bundle))
        run(key,'snapshot','--engine-file',str(engine),'--destination',str(bundle),failure='control_bundle_creation_failed')
        run(new_key,'init-key',database_required=False)
        run(new_key,'restore','--source',str(bundle),'--destination',str(root/'wrong-key'),
            database_required=False,failure='control_vault_authentication_failed')
        assert not (root/'wrong-key').exists()
        run(key,'restore','--source',str(bundle),'--destination',str(restored),database_required=False)
        restored_before = dump(restored/'control.sqlite3')
        run(key,'restore','--source',str(bundle),'--destination',str(restored),
            database_required=False,failure='control_bundle_restore_failed')
        assert dump(restored/'control.sqlite3')==restored_before
        assert dump(restored/'engine.sqlite3')==dump(engine)
        run(key,'rotate-key','--new-key-file',str(new_key))
        run(key,'init',failure='control_vault_authentication_failed')
        run(new_key,'init')
        with ControlStore(database,new_key) as store:
            assert store.resolve_credentials(context,credential)==b'SYNTHETIC_NOT_A_TOKEN'
        with ControlStore(restored/'control.sqlite3',key) as store:
            assert store.resolve_credentials(context,credential)==b'SYNTHETIC_NOT_A_TOKEN'
        assert engine.read_bytes()==engine_before, 'backup/rotation changed engine'
        print('PASS CTRL-UAT-08: CLI snapshot/isolated restore, key rotation and wrong-key/no-overwrite refusals')
    print('PASS: synthetic files cleaned; existing .local/control-stage untouched; no network or real credentials')


if __name__=='__main__':
    main()
