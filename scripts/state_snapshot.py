"""Quiesced SQLite backup/restore to a new private file; never overwrite state."""

import argparse
from contextlib import closing
import fcntl
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

from calendar_sync.schema import MIGRATIONS


class SnapshotError(RuntimeError):
    """Fixed diagnostics only; database contents/paths are never printed."""


SIDECARS = ('.lock', '-wal', '-shm', '-journal')


def _trusted_directories(path):
    for index, directory in enumerate((path, *path.parents)):
        info = directory.stat()
        if info.st_uid not in {0, os.geteuid()}:
            raise SnapshotError('snapshot_untrusted_directory')
        sticky_ancestor = index > 0 and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if info.st_mode & 0o022 and not sticky_ancestor:
            raise SnapshotError('snapshot_untrusted_directory')


def _identity(info):
    return info.st_dev, info.st_ino


def _unchanged(path, original, code):
    try:
        current = path.lstat()
    except FileNotFoundError:
        raise SnapshotError(code) from None
    if (not stat.S_ISREG(current.st_mode) or _identity(current) != _identity(original)
            or current.st_uid != os.geteuid() or current.st_mode & 0o077 or current.st_nlink != 1):
        raise SnapshotError(code)


def _schema(connection):
    return {(kind, name, table): re.sub(r'\s+', ' ', sql).strip() for kind, name, table, sql in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL")}


def _inspect(connection):
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if not 1 <= version <= len(MIGRATIONS):
        raise SnapshotError('snapshot_schema_unsupported')
    expected = {'configurations', 'credential_references', 'runs', 'mappings', 'audit_entries'}
    if version >= 2:
        expected.add('write_intents')
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not expected <= tables:
        raise SnapshotError('snapshot_schema_incomplete')
    with closing(sqlite3.connect(':memory:')) as reference:
        for migration in MIGRATIONS[:version]:
            for statement in migration:
                reference.execute(statement)
        if _schema(connection) != _schema(reference):
            raise SnapshotError('snapshot_schema_mismatch')
    if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise SnapshotError('snapshot_integrity_failed')
    if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise SnapshotError('snapshot_foreign_keys_failed')
    return version, connection.execute('SELECT count(*) FROM mappings').fetchone()[0]


def snapshot(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_absolute() or not destination.is_absolute():
        raise SnapshotError('snapshot_absolute_paths_required')
    if source.is_symlink() or destination.is_symlink():
        raise SnapshotError('snapshot_symlink_refused')
    try:
        info = source.stat()
    except FileNotFoundError:
        raise SnapshotError('snapshot_source_missing') from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        raise SnapshotError('snapshot_private_owned_file_required')
    source, destination = source.resolve(), destination.resolve()
    _trusted_directories(source.parent)
    if source == destination or destination.exists():
        raise SnapshotError('snapshot_destination_exists')
    if any(str(destination) == str(source) + suffix for suffix in SIDECARS):
        raise SnapshotError('snapshot_sidecar_destination_refused')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _trusted_directories(destination.parent)
    directory = destination.parent.stat()
    if directory.st_uid != os.geteuid() or directory.st_mode & 0o077:
        raise SnapshotError('snapshot_private_destination_directory_required')
    if any(os.path.lexists(str(destination) + suffix) for suffix in SIDECARS):
        raise SnapshotError('snapshot_destination_sidecars_present')
    lock_path = Path(str(source) + '.lock')
    lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    lock_info = os.fstat(lock)
    destination_lock = None
    source_fd = None
    temporary = None
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SnapshotError('snapshot_source_in_use') from None
        _unchanged(lock_path, lock_info, 'snapshot_source_lock_changed')
        _unchanged(source, info, 'snapshot_source_changed')
        source_fd = os.open(source, os.O_RDWR | os.O_NOFOLLOW)
        if _identity(os.fstat(source_fd)) != _identity(info):
            raise SnapshotError('snapshot_source_changed')
        destination_lock_path = Path(str(destination) + '.lock')
        try:
            destination_lock = os.open(destination_lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise SnapshotError('snapshot_destination_sidecars_present') from None
        fcntl.flock(destination_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        destination_lock_info = os.fstat(destination_lock)
        def verify_paths():
            _unchanged(source, info, 'snapshot_source_changed')
            _unchanged(lock_path, lock_info, 'snapshot_source_lock_changed')
            _unchanged(destination_lock_path, destination_lock_info, 'snapshot_destination_lock_changed')
            if any(os.path.lexists(str(destination) + suffix) for suffix in SIDECARS[1:]):
                raise SnapshotError('snapshot_destination_sidecars_present')
        # rw opens only an existing source and permits SQLite hot-journal recovery.
        with closing(sqlite3.connect(source.as_uri() + '?mode=rw', uri=True)) as origin:
            verify_paths()
            before = _inspect(origin)
            descriptor, temporary = tempfile.mkstemp(prefix='.snapshot-', dir=destination.parent)
            os.close(descriptor)
            with closing(sqlite3.connect(temporary)) as output:
                origin.backup(output)
                if _inspect(output) != before:
                    raise SnapshotError('snapshot_verification_failed')
            verify_paths()
        with open(temporary, 'rb') as stream:
            os.fsync(stream.fileno())
        verified_info = Path(temporary).stat()
        verify_paths()
        # Same-directory hard link publishes atomically and refuses overwrite races.
        try:
            os.link(temporary, destination)
        except FileExistsError:
            raise SnapshotError('snapshot_destination_exists') from None
        # Never apply an unrelated destination journal; recheck isolation first.
        verify_paths()
        def verify_destination():
            current = destination.lstat()
            if (not stat.S_ISREG(current.st_mode) or _identity(current) != _identity(verified_info)
                    or current.st_uid != os.geteuid() or current.st_mode & 0o077 or current.st_nlink != 2):
                raise SnapshotError('snapshot_destination_changed')
        verify_destination()
        with closing(sqlite3.connect(destination.as_uri() + '?mode=ro', uri=True)) as published:
            if _inspect(published) != before:
                raise SnapshotError('snapshot_verification_failed')
        verify_destination()
        verify_paths()
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return before
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        if source_fd is not None:
            os.close(source_fd)
        if destination_lock is not None:
            os.close(destination_lock)
        os.close(lock)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('backup', 'restore'))
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args(argv)
    try:
        version, mappings = snapshot(args.source, args.destination)
    except SnapshotError as error:
        print(str(error))
        return 2
    except (OSError, sqlite3.Error):
        print('snapshot_io_or_database_failed')
        return 2
    print(f'PASS: {args.operation} verified; schema={version}; mappings={mappings}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
