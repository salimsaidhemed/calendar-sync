"""Local, single-worker persistence, independent of provider and web clients."""

from contextlib import contextmanager
import fcntl
import os
import re
from pathlib import Path
import sqlite3
from uuid import uuid4

from calendar_sync.schema import MIGRATIONS


class StateLockedError(RuntimeError):
    pass


class SchemaVersionError(RuntimeError):
    pass


class StateConflictError(RuntimeError):
    pass


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.connection = None
        self._lock_fd = None

    def __enter__(self):
        if self.connection is not None or self._lock_fd is not None:
            raise RuntimeError('StateStore is already open')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self._lock_fd = os.open(
                str(self.path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
            )
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StateLockedError('State database is already in use') from error
            database_fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(database_fd, 0o600)
            finally:
                os.close(database_fd)
            self.connection = sqlite3.connect(self.path, isolation_level=None)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute('PRAGMA foreign_keys = ON')
            self.connection.execute('PRAGMA synchronous = FULL')
            self._migrate()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, exception_type, exception, traceback):
        self.close()

    def close(self):
        try:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
        finally:
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None

    @contextmanager
    def transaction(self):
        if self.connection is None:
            raise RuntimeError('Open StateStore with a context manager first')
        if self.connection.in_transaction:
            raise RuntimeError('Nested transactions are not supported')
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield self.connection
            self.connection.execute('COMMIT')
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute('ROLLBACK')
            raise

    def _migrate(self):
        with self.transaction() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version > len(MIGRATIONS):
                raise SchemaVersionError('Database schema is newer than this application')
            for index in range(version, len(MIGRATIONS)):
                for statement in MIGRATIONS[index]:
                    connection.execute(statement)
                connection.execute(f'PRAGMA user_version = {index + 1}')

    def create_configuration(self, configuration_id, owner_subject, google_calendar_id,
                             nextcloud_calendar_url, *, google_credential_file,
                             nextcloud_credential_file):
        credential_paths = (google_credential_file, nextcloud_credential_file)
        if not all(Path(path).is_absolute() for path in credential_paths):
            raise ValueError('Credential references must be absolute file paths')
        if not all((configuration_id, owner_subject, google_calendar_id, nextcloud_calendar_url)):
            raise ValueError('Configuration identifiers cannot be empty')
        with self.transaction() as connection:
            connection.execute(
                'INSERT INTO configurations(id,owner_subject,google_calendar_id,'
                'nextcloud_calendar_url) VALUES (?,?,?,?)',
                (configuration_id, owner_subject, google_calendar_id, nextcloud_calendar_url),
            )
            connection.executemany(
                'INSERT INTO credential_references VALUES (?,?,?)',
                [(configuration_id, 'google', str(google_credential_file)),
                 (configuration_id, 'nextcloud', str(nextcloud_credential_file))],
            )

    def start_run(self, configuration_id, *, dry_run=True, purpose='manual'):
        if type(dry_run) is not bool:
            raise ValueError('dry_run must be a boolean')
        if purpose not in {'manual', 'execution'}:
            raise ValueError('Invalid run purpose')
        run_id = str(uuid4())
        with self.transaction() as connection:
            connection.execute(
                'INSERT INTO runs(id,configuration_id,dry_run,purpose) VALUES (?,?,?,?)',
                (run_id, configuration_id, int(dry_run), purpose),
            )
        return run_id

    def _active_run(self, connection, configuration_id, run_id):
        run = connection.execute(
            "SELECT * FROM runs WHERE configuration_id=? AND id=? AND status='running'",
            (configuration_id, run_id),
        ).fetchone()
        if run is None:
            raise ValueError('No active run in this configuration')
        return run

    def append_audit(self, configuration_id, run_id, action, outcome, *, source_event_id=None, reason_code=''):
        if outcome == 'applied':
            raise ValueError('Applied audit entries require record_applied')
        with self.transaction() as connection:
            self._active_run(connection, configuration_id, run_id)
            connection.execute(
                'INSERT INTO audit_entries(configuration_id,run_id,action,outcome,source_event_id,reason_code) '
                'VALUES (?,?,?,?,?,?)',
                (configuration_id, run_id, action, outcome, source_event_id, reason_code),
            )

    def record_applied(self, configuration_id, run_id, *, source_event_id,
                       destination_href, destination_uid, source_fingerprint,
                       destination_etag, action):
        with self.transaction() as connection:
            self._record_applied(connection, configuration_id, run_id,
                source_event_id=source_event_id, destination_href=destination_href,
                destination_uid=destination_uid, source_fingerprint=source_fingerprint,
                destination_etag=destination_etag, action=action)

    def _record_applied(self, connection, configuration_id, run_id, *, source_event_id,
                       destination_href, destination_uid, source_fingerprint,
                       destination_etag, action, reason_code='', operation_id=None):
        if action not in {'create', 'update'}:
            raise ValueError('Applied action must be create or update')
        if not all((source_event_id, destination_href, destination_uid,
                    source_fingerprint, destination_etag)):
            raise ValueError('Applied mapping fields cannot be empty')
        run = self._active_run(connection, configuration_id, run_id)
        if run['dry_run']:
            raise ValueError('Dry-run cannot record applied mappings')
        existing = connection.execute(
            'SELECT * FROM mappings WHERE configuration_id=? AND source_event_id=?',
            (configuration_id, source_event_id),
        ).fetchone()
        if existing and (existing['destination_href'] != destination_href
                         or existing['destination_uid'] != destination_uid):
            raise ValueError('An existing mapping cannot change destination identity')
        connection.execute(
            'INSERT INTO mappings(configuration_id,source_event_id,destination_href,'
            'destination_uid,source_fingerprint,destination_etag,applied_run_id) '
            'VALUES (?,?,?,?,?,?,?) ON CONFLICT(configuration_id,source_event_id) '
            'DO UPDATE SET source_fingerprint=excluded.source_fingerprint,'
            'destination_etag=excluded.destination_etag,applied_run_id=excluded.applied_run_id,'
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (configuration_id, source_event_id, destination_href, destination_uid,
             source_fingerprint, destination_etag, run_id),
        )
        connection.execute(
            'INSERT INTO audit_entries(configuration_id,run_id,action,outcome,source_event_id,reason_code,operation_id) '
            "VALUES (?,?,?,'applied',?,?,?)",
            (configuration_id, run_id, action, source_event_id, reason_code, operation_id),
        )

    def finish_run(self, configuration_id, run_id, status, *, reason_code=''):
        if status not in {'succeeded', 'failed', 'interrupted'}:
            raise ValueError('Invalid terminal run status')
        with self.transaction() as connection:
            self._active_run(connection, configuration_id, run_id)
            connection.execute(
                "UPDATE runs SET status=?,finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                'WHERE configuration_id=? AND id=?', (status, configuration_id, run_id),
            )
            connection.execute(
                "INSERT INTO audit_entries(configuration_id,run_id,action,outcome,reason_code) VALUES (?,?,'run',?,?)",
                (configuration_id, run_id, status, reason_code),
            )

    def get_mapping(self, configuration_id, source_event_id):
        return self.connection.execute(
            'SELECT * FROM mappings WHERE configuration_id=? AND source_event_id=?',
            (configuration_id, source_event_id),
        ).fetchone()

    def planning_snapshot(self, configuration_id):
        with self.transaction() as connection:
            configuration = connection.execute(
                'SELECT id,google_calendar_id,nextcloud_calendar_url FROM configurations WHERE id=?',
                (configuration_id,),
            ).fetchone()
            if configuration is None:
                raise ValueError('Unknown sync configuration')
            mappings = connection.execute(
                'SELECT configuration_id,source_event_id,destination_href,destination_uid,'
                'source_fingerprint,destination_etag FROM mappings '
                'WHERE configuration_id=? ORDER BY source_event_id', (configuration_id,),
            ).fetchall()
            return dict(configuration), tuple(dict(row) for row in mappings)

    def list_audit(self, configuration_id, *, after_id=0, limit=100):
        if not 1 <= limit <= 1000:
            raise ValueError('Audit page limit must be between 1 and 1000')
        return self.connection.execute(
            'SELECT * FROM audit_entries WHERE configuration_id=? AND id>? ORDER BY id LIMIT ?',
            (configuration_id, after_id, limit),
        ).fetchall()

    def interrupt_execution_runs(self, configuration_id):
        rows = self.connection.execute(
            "SELECT id FROM runs WHERE configuration_id=? AND status='running' AND purpose='execution'",
            (configuration_id,),
        ).fetchall()
        for row in rows:
            self.finish_run(configuration_id, row['id'], 'interrupted', reason_code='execution_restarted')

    def pending_intents(self, configuration_id):
        return tuple(dict(row) for row in self.connection.execute(
            "SELECT * FROM write_intents WHERE configuration_id=? AND status='pending' ORDER BY created_at,id",
            (configuration_id,),
        ).fetchall())

    def get_intent(self, configuration_id, operation_id):
        row = self.connection.execute(
            'SELECT * FROM write_intents WHERE configuration_id=? AND id=?',
            (configuration_id, operation_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def _intent_mapping(self, connection, intent):
        mapping = connection.execute(
            'SELECT * FROM mappings WHERE configuration_id=? AND source_event_id=?',
            (intent['configuration_id'], intent['source_event_id']),
        ).fetchone()
        if intent['action'] == 'create':
            valid = mapping is None
        else:
            valid = mapping is not None and all((
                mapping['destination_href'] == intent['destination_href'],
                mapping['destination_uid'] == intent['destination_uid'],
                mapping['destination_etag'] == intent['expected_etag'],
                mapping['source_fingerprint'] == intent['baseline_fingerprint'],
            ))
        if not valid:
            raise StateConflictError('execution_local_mapping_changed')

    def prepare_intent(self, configuration_id, run_id, intent):
        fields = ('id', 'source_event_id', 'action', 'destination_href', 'destination_uid',
                  'source_fingerprint', 'owner_marker', 'payload_hash', 'stamp', 'expected_etag',
                  'baseline_fingerprint')
        values = {field: intent[field] for field in fields}
        values.update(configuration_id=configuration_id, origin_run_id=run_id)
        for field in ('source_fingerprint', 'owner_marker', 'payload_hash'):
            if not isinstance(values[field], str) or not re.fullmatch(r'[0-9a-f]{64}', values[field]):
                raise ValueError('Invalid intent fingerprint')
        with self.transaction() as connection:
            run = self._active_run(connection, configuration_id, run_id)
            if run['dry_run'] or run['purpose'] != 'execution':
                raise ValueError('Intent requires live execution')
            self._intent_mapping(connection, values)
            columns = fields + ('configuration_id', 'origin_run_id')
            connection.execute('INSERT INTO write_intents(' + ','.join(columns) + ') VALUES ('
                               + ','.join('?' for unused in columns) + ')', tuple(values[key] for key in columns))
        return values

    def mark_intent_attempt(self, configuration_id, operation_id, maximum):
        with self.transaction() as connection:
            result = connection.execute(
                "UPDATE write_intents SET attempts=attempts+1 WHERE configuration_id=? AND id=? "
                "AND status='pending' AND attempts<?", (configuration_id, operation_id, maximum),
            )
            return result.rowcount == 1

    def complete_intent(self, configuration_id, operation_id, run_id, observed_etag):
        if not isinstance(observed_etag, str) or not re.fullmatch(r'"[^"\x00-\x20\x7f]*"', observed_etag):
            raise ValueError('Strong observed ETag required')
        with self.transaction() as connection:
            run = self._active_run(connection, configuration_id, run_id)
            if run['dry_run'] or run['purpose'] != 'execution':
                raise ValueError('Intent requires live execution')
            intent = connection.execute(
                "SELECT * FROM write_intents WHERE configuration_id=? AND id=? AND status='pending'",
                (configuration_id, operation_id),
            ).fetchone()
            if intent is None:
                raise StateConflictError('execution_intent_not_pending')
            self._intent_mapping(connection, intent)
            self._record_applied(connection, configuration_id, run_id,
                source_event_id=intent['source_event_id'], destination_href=intent['destination_href'],
                destination_uid=intent['destination_uid'], source_fingerprint=intent['source_fingerprint'],
                destination_etag=observed_etag, action=intent['action'],
                reason_code='write_confirmed', operation_id=operation_id)
            connection.execute(
                "UPDATE write_intents SET status='applied',completed_run_id=?,reason_code='write_confirmed' "
                'WHERE configuration_id=? AND id=?', (run_id, configuration_id, operation_id),
            )

    def note_intent(self, configuration_id, operation_id, run_id, *, reason, conflict=False):
        with self.transaction() as connection:
            run = self._active_run(connection, configuration_id, run_id)
            if run['dry_run'] or run['purpose'] != 'execution':
                raise ValueError('Intent requires live execution')
            intent = connection.execute(
                "SELECT * FROM write_intents WHERE configuration_id=? AND id=? AND status='pending'",
                (configuration_id, operation_id),
            ).fetchone()
            if intent is None:
                raise StateConflictError('execution_intent_not_pending')
            connection.execute(
                'UPDATE write_intents SET status=?,reason_code=?,completed_run_id=? WHERE configuration_id=? AND id=?',
                ('conflict' if conflict else 'pending', reason, run_id if conflict else None,
                 configuration_id, operation_id),
            )
            connection.execute(
                'INSERT INTO audit_entries(configuration_id,run_id,action,outcome,source_event_id,reason_code,operation_id) '
                'VALUES (?,?,?,?,?,?,?)', (configuration_id, run_id, 'conflict' if conflict else intent['action'],
                'skipped' if conflict else 'failed', intent['source_event_id'], reason, operation_id),
            )
