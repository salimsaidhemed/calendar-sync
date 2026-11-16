"""Offline, explicit legacy import and authenticated quiesced bundle recovery."""

from contextlib import contextmanager, closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

from calendar_sync.control import ControlError, ControlStore, Vault, private_directory, private_file, _path
from calendar_sync.schema import MIGRATIONS


ENGINE_SCHEMA_VERSION = 2


def _schema(connection):
    return {(kind,name,table):re.sub(r'\s+',' ',sql).strip() for kind,name,table,sql in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL")}


def _engine_check(connection):
    # Only the frozen v2 engine is supported; never migrate an engine during import.
    if len(MIGRATIONS) != ENGINE_SCHEMA_VERSION or connection.execute('PRAGMA user_version').fetchone()[0] != ENGINE_SCHEMA_VERSION:
        raise ControlError('control_engine_schema_unsupported')
    with closing(sqlite3.connect(':memory:')) as reference:
        for migration in MIGRATIONS:
            for statement in migration:
                reference.execute(statement)
        if _schema(reference) != _schema(connection):
            raise ControlError('control_engine_schema_mismatch')
    if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise ControlError('control_engine_integrity_failed')
    if connection.execute('PRAGMA foreign_key_check').fetchone():
        raise ControlError('control_engine_integrity_failed')


@contextmanager
def locked_engine(path):
    path = _path(path)
    lock = None
    connection = None
    ready = False
    try:
        os.close(private_file(path))  # Existing private engine only, never auto-create it.
        lock = private_file(str(path)+'.lock',create=True,writable=True)
        try:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ControlError('control_engine_locked') from None
        if any(os.path.lexists(str(path)+suffix) for suffix in ('-wal','-shm','-journal')):
            raise ControlError('control_engine_sidecar_requires_recovery')
        connection = sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,isolation_level=None)
        _engine_check(connection)
        ready = True
        yield connection
    except (OSError,sqlite3.Error):
        if ready:
            raise
        raise ControlError('control_engine_read_failed') from None
    finally:
        if connection is not None:
            connection.close()
        if lock is not None:
            os.close(lock)


def engine_digest(connection):
    return hashlib.sha256('\n'.join(connection.iterdump()).encode()).hexdigest()


def import_legacy(store, engine_file, assignments, *, apply=False):
    """Map configuration IDs to already-provisioned owner UUIDs. None quarantines.

    No credentials are read, no engine table is changed. Entire control import is
    one transaction while holding the engine lock; retry is idempotent.
    """
    if not store.maintenance:
        raise ControlError('control_maintenance_required')
    if not isinstance(assignments,dict):
        raise ControlError('control_manifest_invalid')
    if _path(engine_file) == store.path:
        raise ControlError('control_engine_path_invalid')
    with locked_engine(engine_file) as engine:
        configs = engine.execute('SELECT id,owner_subject,google_calendar_id,nextcloud_calendar_url '
                                 'FROM configurations ORDER BY id').fetchall()
        identifiers = {row[0] for row in configs}
        if not set(assignments) <= identifiers:
            raise ControlError('control_manifest_unknown_configuration')
        if any(row[1] != 'operator' for row in configs):
            raise ControlError('control_legacy_owner_requires_review')
        digest = engine_digest(engine)
        saved = store._meta('legacy_engine_digest')
        if saved is not None and saved != digest:
            raise ControlError('control_engine_changed_requires_review')
        for owner in assignments.values():
            if owner is not None and (not isinstance(owner,str) or not store.connection.execute(
                'SELECT 1 FROM owners WHERE id=? AND disabled=0',(owner,)).fetchone()):
                raise ControlError('control_manifest_owner_unavailable')
        total_assigned = sum(assignments.get(identifier) is not None for identifier in identifiers)
        report = dict(configurations=len(configs),assigned=total_assigned,
                      quarantined=len(configs)-total_assigned,applied=bool(apply))
        # Validate the same write plan in preview, then roll it back. No engine writes.
        with store.transaction():
            for identifier, _, google_id, nextcloud_url in configs:
                owner = assignments.get(identifier)
                old = store.connection.execute('SELECT owner_id,status FROM legacy_imports WHERE configuration_id=?',
                                               (identifier,)).fetchone()
                if old and old['owner_id'] is not None:
                    if owner != old['owner_id']:
                        raise ControlError('control_legacy_reassignment_refused')
                    continue
                if owner is None:
                    if not old:
                        store.connection.execute("INSERT INTO legacy_imports VALUES (?,NULL,?,NULL,'quarantined')",
                                                 (identifier,digest))
                        for endpoint in ('google:'+google_id,'nextcloud:'+nextcloud_url):
                            if store.connection.execute('SELECT 1 FROM reservations WHERE endpoint=?',(endpoint,)).fetchone():
                                raise ControlError('control_endpoint_unavailable')
                            try:
                                store.connection.execute('INSERT INTO quarantine_reservations VALUES (?,?)',(endpoint,identifier))
                            except sqlite3.IntegrityError:
                                raise ControlError('control_endpoint_unavailable') from None
                    continue
                references = dict(engine.execute('SELECT provider,file_path FROM credential_references '
                                                  'WHERE configuration_id=?',(identifier,)))
                if set(references) != {'google','nextcloud'}:
                    raise ControlError('control_legacy_credentials_incomplete')
                connection_ids = []
                for provider in ('google','nextcloud'):
                    # Preserve only the protected reference; no token file is opened.
                    payload = json.dumps({'file_path':references[provider]},separators=(',',':')).encode()
                    connection_ids.append(store._insert_connection(owner,provider,'legacy:'+identifier,payload,'legacy_file'))
                pair = store._insert_pair(owner,*connection_ids,
                    ('google:'+google_id,'nextcloud:'+nextcloud_url),engine_id=identifier,legacy=True)
                store.connection.execute('DELETE FROM quarantine_reservations WHERE configuration_id=?',(identifier,))
                store.connection.execute("INSERT OR REPLACE INTO legacy_imports VALUES (?,?,?,?,'paused')",
                                         (identifier,owner,digest,pair))
                store._audit('legacy_import',owner)
            store._put_meta('legacy_engine_digest',digest)
            if not apply:
                # transaction() tolerates an explicit rollback; commit after this is a no-op.
                store.connection.rollback()
        return report


def _sha(path):
    with os.fdopen(private_file(path),'rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def _copy_database(source, target):
    os.close(private_file(target,create=True,exclusive=True,writable=True))
    with closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
        if destination.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ControlError('control_backup_integrity_failed')


def snapshot_bundle(store, engine_file, destination):
    """New private directory only. Key is recovered separately, never bundled."""
    if not store.maintenance:
        raise ControlError('control_maintenance_required')
    target = _path(destination)
    private_directory(target.parent)
    created = False
    try:
        with locked_engine(engine_file) as engine:
            digest = engine_digest(engine)
            expected = store._meta('legacy_engine_digest')
            if expected is not None and expected != digest:
                raise ControlError('control_engine_changed_requires_review')
            target.mkdir(mode=0o700)
            created = True
            _copy_database(store.connection,target/'control.sqlite3')
            _copy_database(engine,target/'engine.sqlite3')
            manifest = json.dumps(dict(version=1,key_id=store.vault.key_id,
                control_sha256=_sha(target/'control.sqlite3'),engine_sha256=_sha(target/'engine.sqlite3'),
                engine_digest=digest),sort_keys=True,separators=(',',':')).encode()
            with os.fdopen(private_file(target/'manifest.enc',create=True,exclusive=True,writable=True),'wb') as stream:
                stream.write(store.vault.seal(manifest,b'control-bundle-v1'))
                stream.flush()
                os.fsync(stream.fileno())
        return dict(control_schema=1,engine_schema=len(MIGRATIONS),key_id=store.vault.key_id)
    except (OSError,sqlite3.Error):
        if created:
            shutil.rmtree(target)
        raise ControlError('control_bundle_creation_failed') from None
    except BaseException:
        if created:
            shutil.rmtree(target)
        raise


def restore_bundle(source, destination, key_file):
    """Verify authenticated manifest and key, restore into a NEW isolated directory."""
    bundle = _path(source)
    target = _path(destination)
    vault = Vault(key_file)
    created = False
    try:
        with os.fdopen(private_file(bundle/'manifest.enc'),'rb') as stream:
            manifest = json.loads(vault.open(stream.read(65536),b'control-bundle-v1'))
        if manifest['version'] != 1 or manifest['key_id'] != vault.key_id:
            raise ControlError('control_bundle_invalid')
        for name in ('control','engine'):
            if _sha(bundle/(name+'.sqlite3')) != manifest[name+'_sha256']:
                raise ControlError('control_bundle_hash_mismatch')
        private_directory(target.parent)
        target.mkdir(mode=0o700)
        created = True
        for name in ('control.sqlite3','engine.sqlite3'):
            with os.fdopen(private_file(bundle/name),'rb') as origin:
                with os.fdopen(private_file(target/name,create=True,exclusive=True,writable=True),'wb') as output:
                    shutil.copyfileobj(origin,output)
                    output.flush()
                    os.fsync(output.fileno())
        with ControlStore(target/'control.sqlite3',key_file,maintenance=True) as control:
            with locked_engine(target/'engine.sqlite3') as engine:
                if engine_digest(engine) != manifest['engine_digest']:
                    raise ControlError('control_bundle_engine_mismatch')
                imported = control._meta('legacy_engine_digest')
                if imported is not None and imported != manifest['engine_digest']:
                    raise ControlError('control_bundle_engine_mismatch')
        return dict(control_schema=1,engine_schema=len(MIGRATIONS))
    except (OSError,sqlite3.Error,KeyError,TypeError,ValueError):
        if created:
            shutil.rmtree(target)
        raise ControlError('control_bundle_restore_failed') from None
    except BaseException:
        if created:
            shutil.rmtree(target)
        raise
