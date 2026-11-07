# SQLite state library

Implemented for issue #10. This is a library for the upcoming worker and provider adapters. The CLI still does not open a database or sync events.

## Storage and lifetime

Use `StateStore` as a context manager with a file path on a local filesystem. The parent directory is created with mode 0700 when missing; the database uses mode 0600. Existing parent directories retain their permissions: operators must restrict directory access. Credential references are absolute paths only; no credential files are opened or secret contents stored by this library.

```python
from calendar_sync.state import StateStore

with StateStore('/data/calendar-sync.sqlite3') as state:
    state.create_configuration(
        'master', 'operator', 'calendar@example.invalid',
        'https://nextcloud.tcadc.com/remote.php/dav/calendars/example/master/',
        google_credential_file='/run/secrets/google-token.json',
        nextcloud_credential_file='/run/secrets/nextcloud-password',
    )
    run_id = state.start_run('master')
    state.append_audit('master', run_id, 'create', 'planned', source_event_id='example')
    state.finish_run('master', run_id, 'succeeded')
```

Configuration creation is insert-only. A duplicate ID is rejected, not silently overwritten. URLs and credential paths must later be validated by the configuration/provider layer before network use. The stored owner subject supports future Keycloak ownership; it is not authentication or authorization. Callers must authorize access before passing a configuration ID to the repository.

## Schema and atomicity

`schema.py` holds ordered migrations. `PRAGMA user_version` records the schema version. Opening the store enables foreign keys and synchronous FULL, then applies pending migrations in one explicit immediate transaction. A failed migration rolls back both schema changes and the version. A database newer than this application is rejected without downgrade. Do not edit applied migrations; append a new version and test upgrades with existing data.

- Configurations store owner and selected calendars.
- Credential references store one absolute secret-file path per provider per configuration.
- Runs record dry-run status, lifecycle and UTC timestamps.
- Mappings have a configuration/source primary key, unique destination href and UID per configuration, applied source fingerprint, destination ETag and applying run ID.
- Audit entries have configuration/run foreign keys, action/outcome codes, optional source ID and UTC timestamp. They omit event bodies and free-form errors. Event IDs, calendar IDs and paths can still be sensitive; protect the volume and backups.

`record_applied` accepts only an active live run. It writes the mapping and applied audit record in one transaction; either both commit or neither does. A mapping's destination identity cannot be changed through this API. Database triggers also reject mapping inserts/updates tied to dry-run or completed runs. Repeated updates retain one mapping per source. The execution layer must only call this after verifying remote success, ownership and ETags; the state library cannot verify remote writes or prevent duplicates at the provider itself.

`append_audit` can record planned, skipped or failed actions but rejects `applied`; applied records must accompany mapping writes. `finish_run` records a terminal status and its audit entry atomically. Completed runs reject further repository writes. After a crash an unfinished run remains `running`; future worker recovery must reconcile uncertain remote writes before finishing it as interrupted. No automatic recovery is attempted here.

Audit updates and deletes are rejected by triggers. No retention cleanup is implemented yet: define a documented retention/export policy and migration before production use. Append-only protection is an application invariant, not a tamper-proof security boundary against someone who can edit the database or schema.

## Locking and transaction boundaries

Opening the store acquires a nonblocking exclusive POSIX `flock` on `<database>.lock` for its entire lifetime. A second store/process raises `StateLockedError` before migration or writes. Closing the context or process termination releases the operating-system lock. The lock file remains intentionally: do not unlink it while any process may hold it, as that can defeat locking.

Use one store in one process/thread per database; do not fork with an open store. Linux containers and macOS are supported; Windows and shared/network filesystems are not. Do not use alternate hard links to the same database or share its volume across replicas. Cooperating clients must all use this lock; unrelated SQLite clients can bypass it. Future web services must access state through a worker/service boundary rather than open a competing store.

The `transaction` context commits on success and rolls back on exceptions. Nested transactions and repository methods called inside an external transaction are rejected. It is an internal trusted integration hook, not an API to expose to web users. Configuration isolation is enforced by scoped queries and composite foreign keys, not SQLite row-level security.

## Backup and recovery

The library uses SQLite's default rollback journal, not WAL. For a simple offline backup, stop every database writer, close the store, and copy the database to protected storage. If backing up live, use SQLite's backup API from the owning process; copying an active database alone is unsafe. An interrupted transaction can leave a hot journal: retain database and journal together for SQLite recovery rather than deleting the journal. Do not delete or replace a live database or its lock file.

Restore while all clients are stopped, preserve restrictive directory/file permissions, then open with the same or newer compatible application. Migrations run automatically on the next library open. Back up before upgrading. Downgrading a newer schema is unsupported. The lock file is not state and need not be backed up; existing audit entries and unfinished run history are retained. Lost mappings require ownership-aware remote reconciliation in the future engine; restoring an empty database is not permission to recreate every destination event.

No delete/sync execution path, provider client, authorization UI, credential refresh, automatic retention or resumption worker is introduced by this change.
