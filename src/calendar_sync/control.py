"""POSIX-only control repositories. Trusted backend/operator callers, not HTTP auth.

No provider I/O or engine writes. Maintenance and normal handles cooperate through
a separate lock; SQLite transactions serialize concurrent normal writers.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class ControlError(RuntimeError):
    """Fixed diagnostic only: never interpolate provider or credential values."""


@dataclass(frozen=True)
class OwnerContext:
    """Must be constructed by a verified identity boundary, never request JSON."""
    issuer: str
    subject: str


def _text(value, maximum=2048):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ControlError('control_invalid_input')
    return value


def _path(value):
    path = Path(value).absolute()
    for candidate in (path,*path.parents):
        if candidate.is_symlink():
            # macOS /var and /tmp are OS-managed aliases; user-created links are not.
            parent = candidate.parent.stat()
            if candidate == path or candidate.lstat().st_uid != 0 or parent.st_uid != 0 or parent.st_mode & 0o022:
                raise ControlError('control_unsafe_path')
    resolved = path.resolve()
    for ancestor in resolved.parents:
        if not ancestor.exists():
            continue
        info = ancestor.stat()
        sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if info.st_uid not in (0,os.geteuid()) or (info.st_mode & 0o022 and not sticky_root):
            raise ControlError('control_untrusted_directory')
    return resolved


def private_directory(value):
    path = _path(value)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ControlError('control_private_directory_required')
    return path


def private_file(value, *, create=False, exclusive=False, writable=False):
    path = _path(value)
    parent = path.parent.stat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise ControlError('control_private_directory_required')
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT
    if exclusive:
        flags |= os.O_EXCL
    fd = os.open(path, flags, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        os.close(fd)
        raise ControlError('control_private_file_required')
    return fd


def create_key(path):
    """Explicit first-time operator operation. Never replace a recovery key."""
    private_directory(Path(path).absolute().parent)
    try:
        fd = private_file(path, create=True, exclusive=True, writable=True)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(AESGCM.generate_key(bit_length=256))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise ControlError('control_key_creation_failed') from None


class Vault:
    def __init__(self, key_file):
        self.path = _path(key_file)
        try:
            with os.fdopen(private_file(self.path), 'rb') as stream:
                key = stream.read(33)
        except OSError:
            raise ControlError('control_key_unavailable') from None
        if len(key) != 32:
            raise ControlError('control_key_invalid')
        self.key_id = hashlib.sha256(key).hexdigest()
        self._cipher = AESGCM(key)

    def seal(self, payload, associated):
        if not isinstance(payload, bytes) or not 0 < len(payload) <= 65536:
            raise ControlError('control_secret_invalid')
        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(nonce, payload, associated)

    def open(self, envelope, associated):
        try:
            return self._cipher.decrypt(envelope[:12], envelope[12:], associated)
        except (InvalidTag, ValueError, TypeError):
            raise ControlError('control_vault_authentication_failed') from None


APPLICATION_ID = 0x43534354
SCHEMA_VERSION = 1
SCHEMA = (
    'CREATE TABLE metadata(name TEXT PRIMARY KEY, value BLOB NOT NULL)',
    '''CREATE TABLE owners(id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL,
       disabled INTEGER NOT NULL DEFAULT 0 CHECK(disabled IN (0,1)), UNIQUE(issuer,subject))''',
    '''CREATE TABLE connections(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES owners(id),
       provider TEXT NOT NULL CHECK(provider IN ('google','nextcloud')), account_id TEXT NOT NULL,
       status TEXT NOT NULL CHECK(status IN ('ready','needs_verification','disconnected')),
       revision INTEGER NOT NULL DEFAULT 1, kind TEXT NOT NULL CHECK(kind IN ('credential','legacy_file')),
       envelope BLOB NOT NULL, UNIQUE(owner_id,provider,account_id))''',
    '''CREATE TABLE pairs(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES owners(id),
       google_connection TEXT NOT NULL REFERENCES connections(id),
       nextcloud_connection TEXT NOT NULL REFERENCES connections(id),
       google_endpoint TEXT NOT NULL, nextcloud_endpoint TEXT NOT NULL,
       engine_configuration_id TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL DEFAULT 1,
       state TEXT NOT NULL DEFAULT 'paused' CHECK(state IN ('paused','archived')),
       dry_run INTEGER NOT NULL DEFAULT 1 CHECK(dry_run=1),
       direction TEXT NOT NULL DEFAULT 'google_to_nextcloud' CHECK(direction='google_to_nextcloud'),
       legacy_review INTEGER NOT NULL DEFAULT 0 CHECK(legacy_review IN (0,1)))''',
    '''CREATE TABLE reservations(endpoint TEXT PRIMARY KEY, pair_id TEXT NOT NULL REFERENCES pairs(id))''',
    '''CREATE TABLE legacy_imports(configuration_id TEXT PRIMARY KEY,
       owner_id TEXT REFERENCES owners(id), source_digest TEXT NOT NULL,
       pair_id TEXT REFERENCES pairs(id), status TEXT NOT NULL CHECK(status IN ('quarantined','paused')))''',
    '''CREATE TABLE quarantine_reservations(endpoint TEXT PRIMARY KEY,
       configuration_id TEXT NOT NULL REFERENCES legacy_imports(configuration_id))''',
    '''CREATE TABLE security_audit(id INTEGER PRIMARY KEY, owner_id TEXT REFERENCES owners(id),
       action TEXT NOT NULL CHECK(action IN ('owner_created','owner_disabled','connection_created',
       'connection_rotated','connection_disconnected','pair_created','pair_archived','legacy_import','key_rotated')),
       created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))''',
    '''CREATE TRIGGER security_no_update BEFORE UPDATE ON security_audit
       BEGIN SELECT RAISE(ABORT,'security audit retained'); END''',
    '''CREATE TRIGGER security_no_delete BEFORE DELETE ON security_audit
       BEGIN SELECT RAISE(ABORT,'security audit retained'); END''',
)


def schema_signature(connection):
    return {(row[0],row[1],row[2]):' '.join(row[3].split()) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL")}


class ControlStore:
    def __init__(self, path, key_file, *, maintenance=False):
        self.path = _path(path)
        self.vault = Vault(key_file)
        if self.path.parent == self.vault.path.parent:
            raise ControlError('control_key_must_be_separate')
        self.maintenance = maintenance
        self.connection = None
        self._lock = None

    def __enter__(self):
        if self.connection is not None or self._lock is not None:
            raise ControlError('control_already_open')
        try:
            private_directory(self.path.parent)
            self._lock = private_file(str(self.path)+'.control-lock', create=True, writable=True)
            try:
                fcntl.flock(self._lock, (fcntl.LOCK_EX if self.maintenance else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ControlError('control_maintenance_locked') from None
            for suffix in ('-wal', '-shm', '-journal'):
                if os.path.lexists(str(self.path)+suffix):
                    raise ControlError('control_sidecar_requires_recovery')
            os.close(private_file(self.path, create=True, writable=True))
            self.connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute('PRAGMA foreign_keys=ON')
            self.connection.execute('PRAGMA synchronous=FULL')
            with self.transaction():
                version = self.connection.execute('PRAGMA user_version').fetchone()[0]
                app = self.connection.execute('PRAGMA application_id').fetchone()[0]
                tables = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if version == 0 and app == 0 and not tables:
                    for statement in SCHEMA:
                        self.connection.execute(statement)
                    self.connection.execute(f'PRAGMA application_id={APPLICATION_ID}')
                    self.connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                    self._put_meta('key_check', self.vault.seal(b'control-v1', b'control-key-check'))
                elif version != SCHEMA_VERSION or app != APPLICATION_ID:
                    raise ControlError('control_schema_unsupported')
                reference = sqlite3.connect(':memory:')
                try:
                    for statement in SCHEMA:
                        reference.execute(statement)
                    if schema_signature(reference) != schema_signature(self.connection):
                        raise ControlError('control_schema_mismatch')
                finally:
                    reference.close()
                if [tuple(row) for row in self.connection.execute('PRAGMA quick_check')] != [('ok',)] or \
                        self.connection.execute('PRAGMA foreign_key_check').fetchone():
                    raise ControlError('control_integrity_failed')
                if self.vault.open(self._meta('key_check'), b'control-key-check') != b'control-v1':
                    raise ControlError('control_vault_authentication_failed')
            return self
        except (OSError, sqlite3.Error):
            self.close()
            raise ControlError('control_storage_failed') from None
        except BaseException:
            self.close()
            raise

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    @contextmanager
    def transaction(self):
        if self.connection is None or self.connection.in_transaction:
            raise ControlError('control_transaction_required')
        try:
            self.connection.execute('BEGIN IMMEDIATE')
            yield
            self.connection.commit()
        except BaseException as error:
            self.connection.rollback()
            if isinstance(error, sqlite3.IntegrityError):
                raise ControlError('control_conflict') from None
            if isinstance(error, sqlite3.Error):
                raise ControlError('control_storage_failed') from None
            raise

    def _meta(self, name):
        row = self.connection.execute('SELECT value FROM metadata WHERE name=?', (name,)).fetchone()
        return row[0] if row else None

    def _put_meta(self, name, value):
        self.connection.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', (name,value))

    def _audit(self, action, owner=None):
        self.connection.execute('INSERT INTO security_audit(owner_id,action) VALUES (?,?)', (owner,action))

    def register_owner(self, context):
        """Operator/verified OIDC provisioning only; this does not validate a JWT."""
        if not isinstance(context, OwnerContext):
            raise ControlError('control_owner_required')
        _text(context.issuer)
        _text(context.subject)
        with self.transaction():
            row = self.connection.execute('SELECT id FROM owners WHERE issuer=? AND subject=?',
                                          (context.issuer, context.subject)).fetchone()
            if row:
                return row[0]
            identifier = uuid4().hex
            self.connection.execute('INSERT INTO owners(id,issuer,subject) VALUES (?,?,?)',
                                    (identifier,context.issuer,context.subject))
            self._audit('owner_created', identifier)
            return identifier

    def _owner(self, context):
        if not isinstance(context, OwnerContext):
            raise ControlError('control_owner_required')
        row = self.connection.execute('SELECT id FROM owners WHERE issuer=? AND subject=? AND disabled=0',
                                     (context.issuer,context.subject)).fetchone()
        if row is None:
            raise ControlError('control_owner_unavailable')
        return row[0]

    def disable_owner(self, context):
        """Operator operation. There is deliberately no enable/live operation yet."""
        with self.transaction():
            owner = self._owner(context)
            self.connection.execute('UPDATE owners SET disabled=1 WHERE id=?', (owner,))
            self._audit('owner_disabled', owner)

    @staticmethod
    def _aad(row):
        return json.dumps([row['id'],row['owner_id'],row['provider'],row['kind'],row['revision']],
                          separators=(',',':')).encode()

    def _insert_connection(self, owner, provider, account, payload, kind='credential'):
        if provider not in ('google','nextcloud'):
            raise ControlError('control_invalid_provider')
        row = dict(id=uuid4().hex, owner_id=owner, provider=provider, kind=kind, revision=1)
        envelope = self.vault.seal(payload, self._aad(row))
        self.connection.execute('INSERT INTO connections VALUES (?,?,?,?,?,1,?,?)',
            (row['id'],owner,provider,_text(account),'ready' if kind=='credential' else 'needs_verification',kind,envelope))
        self._audit('connection_created', owner)
        return row['id']

    def create_connection(self, context, provider, verified_account_id, payload):
        """Backend connector only: account identity/permissions must be verified upstream."""
        with self.transaction():
            return self._insert_connection(self._owner(context),provider,verified_account_id,payload)

    def _connection(self, owner, identifier):
        row = self.connection.execute('SELECT * FROM connections WHERE id=? AND owner_id=?',
                                     (identifier,owner)).fetchone()
        if row is None:
            raise ControlError('control_not_found')
        return row

    def list_connections(self, context):
        owner = self._owner(context)
        return [dict(row) for row in self.connection.execute(
            'SELECT id,provider,account_id,status,revision FROM connections WHERE owner_id=? ORDER BY id', (owner,))]

    def resolve_credentials(self, context, connection_id):
        """Trusted connector resolver, never a route/serialization method.

        Worker assignment authorization is a separate #34 boundary, not provided here.
        """
        with self.transaction():
            row = self._connection(self._owner(context),connection_id)
            if row['status'] != 'ready' or row['kind'] != 'credential':
                raise ControlError('control_connection_unavailable')
            return self.vault.open(row['envelope'],self._aad(row))

    def replace_credentials(self, context, connection_id, expected_revision, payload):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ControlError('control_invalid_input')
        with self.transaction():
            owner = self._owner(context)
            row = self._connection(owner,connection_id)
            if row['status'] != 'ready' or row['kind'] != 'credential':
                raise ControlError('control_connection_unavailable')
            if row['revision'] != expected_revision:
                raise ControlError('control_revision_changed')
            updated = dict(row, revision=row['revision']+1)
            envelope = self.vault.seal(payload,self._aad(updated))
            self.connection.execute('UPDATE connections SET revision=?,envelope=? WHERE id=?',
                                    (updated['revision'],envelope,connection_id))
            self._audit('connection_rotated', owner)
            return updated['revision']

    def disconnect(self, context, connection_id):
        with self.transaction():
            owner = self._owner(context)
            row = self._connection(owner,connection_id)
            # No ciphertext rewrite: lifecycle does not alter AAD revision.
            self.connection.execute("UPDATE connections SET status='disconnected' WHERE id=?", (row['id'],))
            self._audit('connection_disconnected', owner)

    def _insert_pair(self, owner, google, nextcloud, endpoints, engine_id=None, legacy=False):
        for connection, provider in ((google,'google'),(nextcloud,'nextcloud')):
            row = self._connection(owner,connection)
            expected = 'needs_verification' if legacy else 'ready'
            if row['provider'] != provider or row['status'] != expected:
                raise ControlError('control_connection_unavailable')
        if not isinstance(endpoints,(tuple,list)) or len(endpoints)!=2:
            raise ControlError('control_invalid_input')
        for value,prefix in zip(endpoints, ('google:', 'nextcloud:')):
            if not _text(value).startswith(prefix) or value == prefix:
                raise ControlError('control_invalid_endpoint')
            quarantined = self.connection.execute(
                'SELECT configuration_id FROM quarantine_reservations WHERE endpoint=?',(value,)).fetchone()
            if quarantined is not None and quarantined[0] != engine_id:
                raise ControlError('control_endpoint_unavailable')
        identifier = uuid4().hex
        self.connection.execute('''INSERT INTO pairs(id,owner_id,google_connection,nextcloud_connection,
            google_endpoint,nextcloud_endpoint,engine_configuration_id,legacy_review) VALUES (?,?,?,?,?,?,?,?)''',
            (identifier,owner,google,nextcloud,*endpoints,engine_id or uuid4().hex,int(legacy)))
        for endpoint in endpoints:
            try:
                self.connection.execute('INSERT INTO reservations VALUES (?,?)', (endpoint,identifier))
            except sqlite3.IntegrityError:
                raise ControlError('control_endpoint_unavailable') from None
        self._audit('pair_created', owner)
        return identifier

    def create_pair(self, context, google_connection, nextcloud_connection, canonical_endpoints):
        """Trusted discovery service supplies canonical identities, NOT user URLs.

        This foundation cannot activate pairs; live permission/discovery checks are #37.
        """
        with self.transaction():
            return self._insert_pair(self._owner(context),google_connection,nextcloud_connection,canonical_endpoints)

    def get_pair(self, context, pair_id):
        row = self.connection.execute('SELECT id,revision,state,dry_run,direction,legacy_review '
            'FROM pairs WHERE owner_id=? AND id=?', (self._owner(context),pair_id)).fetchone()
        if row is None:
            raise ControlError('control_not_found')
        return dict(row)

    def archive_pair(self, context, pair_id, expected_revision):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ControlError('control_invalid_input')
        with self.transaction():
            owner = self._owner(context)
            pair = self.get_pair(context,pair_id)
            if pair['revision'] != expected_revision:
                raise ControlError('control_revision_changed')
            self.connection.execute("UPDATE pairs SET state='archived',revision=revision+1 WHERE id=?", (pair_id,))
            self._audit('pair_archived', owner)

    def list_audit(self, context, *, after_id=0, limit=50):
        if type(after_id) is not int or after_id<0 or type(limit) is not int or not 1<=limit<=100:
            raise ControlError('control_invalid_input')
        return [dict(row) for row in self.connection.execute('SELECT id,action,created_at FROM security_audit '
            'WHERE owner_id=? AND id>? ORDER BY id LIMIT ?', (self._owner(context),after_id,limit))]

    def rotate_key(self, new_key_file):
        if not self.maintenance:
            raise ControlError('control_maintenance_required')
        replacement = Vault(new_key_file)
        if replacement.path.parent == self.path.parent or replacement.key_id == self.vault.key_id:
            raise ControlError('control_key_must_be_separate')
        with self.transaction():
            for row in self.connection.execute('SELECT * FROM connections').fetchall():
                plaintext = self.vault.open(row['envelope'],self._aad(row))
                self.connection.execute('UPDATE connections SET envelope=? WHERE id=?',
                    (replacement.seal(plaintext,self._aad(row)),row['id']))
            self._put_meta('key_check',replacement.seal(b'control-v1',b'control-key-check'))
            self._audit('key_rotated')
        self.vault = replacement
