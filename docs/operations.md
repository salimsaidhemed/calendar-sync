# Setup, backup and recovery runbook

Issue #16 completes the operating instructions for the one-master worker. It does not release v0.1.0, introduce a UI, or establish production acceptance. The worker dependency, PR #30, is merged; the operations PR now targets main for owner review. For a customer-controlled Workspace deployment use [customer-production-deployment.md](customer-production-deployment.md). Release/safety sign-off remains #14 and independent reviewer identity remains #8.

## Deployment checklist — OPS-UAT-01

Use Python3.12+ on Linux/macOS, or the unprivileged Linux image. Keep one process/replica per local SQLite database. Shared/network filesystems, Windows, multiple replicas on one state volume and concurrent web embedding are unsupported. The Docker root filesystem is read-only; only private state/token storage and temporary /tmp need writes. No provider credentials are required for development/unit tests.

Choose **one** authorization path in [Google setup](google-setup.md#choose-the-account-and-environment): personal testing (SETUP-PT), Workspace testing (WT), personal production authorization (PP), or Workspace production authorization (WP). Record the path and actual account class; a Gmail/Workspace-looking address alone does not determine Cloud-project audience. Google requires Calendar API enablement in the selected project, a matching Desktop OAuth client, read-only event scope, exact source calendar ID and event-detail access. Workspace admin controls may separately restrict the client/user/scope/sharing. No service account/domain-wide delegation, Gmail access or Google event writes are used.

For External/Testing, add the exact test user and plan for seven-day calendar-grant expiry; Internal depends on eligible organization membership/admin policy. External production publishing/verification or a valid exemption must be resolved for your actual audience; production tokens remain revocable. Do not publish merely to bypass a test-user error. [Google token lifecycle](https://developers.google.com/identity/protocols/oauth2#expiration) and [Workspace API controls](https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data) are the authoritative policy references.

Authorize on the workstation with the browser, outside the headless worker. Use distinct private client and token paths; directories0700/files0600 owned by that process user. Copy only the needed token into the private runtime storage through a protected channel; it needs a writable parent for its lock and atomic refresh. Keep OAuth client material out of the image and browser UI. A missing/rejected token requires deliberate workstation reauthorization, not interactive fallback in the worker.

In [Nextcloud setup](nextcloud.md#set-up-a-dedicated-nextcloud-test-calendar), select the **exact** CalDAV collection URL (not display name/browser/export/public-feed URL) and a login with event read/write access. Use a dedicated device/app password from personal Security settings, not a browser/Keycloak SSO session. Account/device tokens may authorize more than one calendar: explicit client URL scoping is not a server-side per-calendar permission boundary. Prefer a dedicated limited account/calendar share. See [Nextcloud device passwords](https://docs.nextcloud.com/server/stable/user_manual/en/session_management.html).

Production uses verified HTTPS at the trusted origin (default https://nextcloud.tcadc.com), never TLS bypass or LAN HTTP. Loopback HTTP is a deliberate **local test** exception requiring matching origin/calendar and explicit opt-in; container localhost is its network namespace. Use [the reproducible Docker UAT setup](docker-uat.md), including private credential-volume preparation, for the isolated same-engine localhost:8090 route. Do not reuse its host-network/HTTP manifest as a production default.

Record privately: configuration ID, exact source/destination identities, runtime credential **paths** (not contents), state/health paths, image ID/commit, account/setup path and backup owner. IDs/URLs/paths can be sensitive; do not paste them into public issue evidence. Keep references stable across restarts. The current state repository stores an operator subject for future owner scoping; it does not implement Keycloak authentication/authorization.

## Start safely and assess health — OPS-UAT-02

Prepare `.env` from [.env.example](../.env.example), dedicated mounted credentials/ownership, and the worker settings in [worker.md](worker.md). On native Linux, the runtime files/directories must be owned by UID10001. On Mac use the UAT credential volumes instead of changing ownership of your working host files. Shell paths containing `$PWD` need double quotes; local Python does not automatically load .env. Production Compose consumes .env explicitly; UAT uses its separate environment file/helper.

From repository root, with Docker running:

```sh
prod() { docker compose --env-file .env -f compose.yaml "$@"; }
prod config --quiet
prod build worker
prod run --rm --no-deps worker calendar-sync worker --once
```

Keep SYNC_DRY_RUN=true until a complete preview matches known selected calendars. Health alone is insufficient: an accidentally selected empty source can be healthy with all-zero proposals; unsupported/missing events can also be healthy. Verify actual counts, event type/date/time and exact fixture/calendar assignment. Live executor writes require literal false and explicit operator review; changing an exported variable or .env without recreating a container does not update its environment.

After the reviewed preview, set the desired mode in .env, then:

```sh
prod up -d --force-recreate worker
prod logs --tail 20 worker
prod exec worker calendar-sync health
echo "Health exit code: $?"
prod ps
```

Exit0 means recent completed conflict-free last-cycle health, not process liveness, complete field synchronization or applied writes in dry-run. Starting/stale/stopped/unhealthy/malformed health returns1; once-cycle failure returns1; startup/state/config failure returns2. Health checks never open SQLite or providers. Docker does not restart a running process just because health is unhealthy; alert an operator on persistent failure. Runtime logs contain fixed codes/counts, never raw provider messages. Read-only adapter diagnostics are in [docker-uat.md](docker-uat.md#read-only-diagnostics-and-preview--docker-uat-03) and the provider guides.

The worker retries transient cycles with bounded jittered backoff; provider Retry-After can defer up to24h beyond the normal backoff cap. An uncertain write stays journaled; each intent has a durable three-PUT budget. Exhaustion is not permission to reset attempts. SIGTERM/SIGINT stop new work and allow in-flight confirmation. Existing network timeouts are per connect/read, not a whole-cycle deadline; forced termination may require recovery. Retain state and diagnose failures, rather than resetting volumes/configuration IDs.

## Quiesced backup — OPS-UAT-03

The bundled [state_snapshot.py](../scripts/state_snapshot.py) is a local-file helper: no providers/credentials are opened and no calendar writes occur. Both `backup` and `restore` copy SQLite consistently through its backup API to a **new** private file. They compare the tables/constraints/indexes/triggers to the supported migration schema, validate quick_check and foreign keys, and retain mappings/runs/audit/pending intents. They refuse active source locks, symlinks/hard-linked source files, untrusted directory ancestry, changed source/lock identities, unsafe permissions/sidecar targets and existing destinations. The destination must also have **no existing lock/journal/WAL/SHM**; a fresh private restore directory is preferred. A reserved destination lock guards publication; published identity/schema are checked before PASS. Source schema is not migrated. SQLite may perform normal source hot-journal recovery, so preserve the database and journal together first. This helper rejects unsupported/manual schema changes and corruption; it is not a forensic repair tool or a security boundary against a malicious process running as the same user/root.

Stop **every** application client first. The helper takes the same POSIX source lock but cannot prevent unrelated SQLite clients or a user bypassing that lock. Do not remove/recreate an active lock or copy an active database file by itself. Retain any hot journal/-wal/-shm until SQLite safely handles it; never mix old sidecars into a restored directory. See [SQLite corruption guidance](https://www.sqlite.org/howtocorrupt.html) and [backup API](https://www.sqlite.org/backup.html).

For local Python (same app user, activated venv), set actual absolute paths. Choose a new unique destination filename every time:

```sh
export SYNC_STATE_FILE='/absolute/private/state/calendar-sync.sqlite3'
export SYNC_BACKUP_FILE='/absolute/private/backups/pre-upgrade.sqlite3'
python scripts/state_snapshot.py backup \
  --source "$SYNC_STATE_FILE" --destination "$SYNC_BACKUP_FILE"
```

Expected PASS with schema version and aggregate mapping count; source contents/schema stay unchanged apart from normal SQLite journal recovery. The helper creates private backup directories when missing, publishes a verified0600 snapshot atomically and refuses overwrite. Existing destination directories must be private/owned by the caller. Immediate source/destination directories cannot permit untrusted writes; root-owned sticky temporary directories are allowed only as ancestors of a protected directory, never directly as the database's parent. Source/destination lock files are intentionally retained, including reservations from failed attempts; do not unlink active locks. An I/O/verification error returns2 with a fixed code; a file/reservation left after a late failure is not a verified backup. Preserve that evidence and use a new filename after investigation, not overwrite retries or sidecar deletion.

For **default-path production Compose**, run as container UID10001 against its mounted state volume. The script is deliberately mounted read-only rather than included in the worker image:

```sh
prod stop worker
prod run --rm --no-deps --volume "$PWD/scripts:/checks:ro" worker \
  python /checks/state_snapshot.py backup \
  --source /data/calendar-sync.sqlite3 \
  --destination /data/backups/pre-upgrade.sqlite3
```

Adapt the source to the actual configured state path and use a fresh destination filename. This copy is on the same volume and is **not disaster recovery**. Transfer verified backups through a protected channel into encrypted, access-controlled independent storage; include backup time/checksum, compatible image/commit and protected configuration references in a manifest. Never print token contents or ship them in Git/image layers. Back up runtime credentials/config separately under an appropriate secret policy; SQLite contains references, not the tokens themselves.

## Restore drill and rollback — OPS-UAT-04

First run a disposable offline restore drill; do not improvise on production. Keep all workers stopped, preserve the failed/current state as a **separate verified backup**, and use the same or newer compatible application. Opening StateStore/worker automatically migrates, even in dry-run. Record the old image before upgrading and take a pre-upgrade snapshot before any new-version open. Never lower user_version or run old code against a newer schema.

Restore into a **new private directory/file**, retaining the original database untouched:

```sh
export SYNC_RESTORE_FILE='/absolute/private/restored/calendar-sync.sqlite3'
python scripts/state_snapshot.py restore \
  --source "$SYNC_BACKUP_FILE" --destination "$SYNC_RESTORE_FILE"
```

For default-path production Compose:

```sh
prod run --rm --no-deps --volume "$PWD/scripts:/checks:ro" worker \
  python /checks/state_snapshot.py restore \
  --source /data/backups/pre-upgrade.sqlite3 \
  --destination /data/recovered/calendar-sync.sqlite3
```

Expected matching schema/mapping count and a private fresh SQLite file. Validate integrity/counts in the copied database while clients remain stopped; the local block below emits only aggregate metadata:

```sh
python - <<'PY'
import os
import sqlite3
from contextlib import closing
from pathlib import Path

path = Path(os.environ['SYNC_RESTORE_FILE']).resolve()
with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
    assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in ('configurations', 'mappings', 'runs', 'audit_entries', 'write_intents'):
        if name in tables:
            print(name + ':', db.execute('SELECT count(*) FROM ' + name).fetchone()[0])
print('PASS: isolated restore integrity and foreign keys')
PY
```

Compare all counts, schema, pending-intent attempts and identities privately against the backup manifest. The automated restore case additionally compares every table/trigger and preserves pending intents/append-only audit. A backup file remains a backup: restore to a new file rather than opening/migrating the only backup through StateStore.

To select a restored copy, set SYNC_STATE_FILE to that new path (container example /data/recovered/calendar-sync.sqlite3) in the correct environment file, retain the **same configuration/calendar IDs and runtime credential references**, set SYNC_DRY_RUN=true, and recreate with `prod up -d --force-recreate worker`. Check the full preview, not only health. Deliberately enable live mode only after reviewing remote divergence/pending intents; dry-run never completes recovery.

**Restoring SQLite does not undo remote writes.** An older snapshot can lack successful mappings or operation evidence; deterministic collisions become conflicts, not automatic adoption. Keep the post-failure snapshot for investigation. Do not clear mappings, change configuration IDs, synthesize ETags, reset attempt counters, delete destination events or send unconditional writes to make a restore look healthy. Lost-state conflicts need reviewed reconciliation; there is no general adoption/repair command.

For an application rollback, only use a preserved **compatible worker image** and its matching pre-upgrade snapshot at a new state path. No older released worker is assumed to exist. If one was recorded, tag its verified local image ID and select it through an explicit override:

```sh
docker image tag "${REVIEWED_ROLLBACK_IMAGE_ID:?Set the recorded compatible worker image ID}" calendar-sync:rollback-reviewed
```

Create a reviewed local `compose.rollback.yaml` containing `services: {worker: {image: 'calendar-sync:rollback-reviewed'}}`. With .env pointing at the matching restored copy and dry-run true:

```sh
docker compose --env-file .env -f compose.yaml -f compose.rollback.yaml up -d --no-build worker
```

Never use --build/pull as a substitute for the recorded image, mix newer DB schema with older code, or remove failed-deployment evidence. If no compatible snapshot/image exists, stop and investigate. Retain the original files/volumes until the restored deployment and remote state are reviewed.

## Scoped credential-path relocation — OPS-UAT-05

Replacing credential **contents at the same runtime path** needs no database change. Changing local absolute paths to container paths does: startup deliberately rejects different stored references. There is no configuration-edit UI/API yet. Use this controlled offline transaction only for relocation within the **same authorized configuration and same exact calendars**. Back up first with OPS-UAT-03, stop every client and keep a protected operator change record (actor/time/ticket/old/new paths/backup identity). It does not create a new mapping or claim a calendar write in audit history.

Set SYNC_CONFIGURATION_ID, GOOGLE_CALENDAR_ID, NEXTCLOUD_CALENDAR_URL, SYNC_STATE_FILE, GOOGLE_TOKEN_FILE and NEXTCLOUD_PASSWORD_FILE to the intended runtime values. Supply OLD_GOOGLE_TOKEN_FILE and OLD_NEXTCLOUD_PASSWORD_FILE as the exact previously stored strings, not guesses. Run in the intended runtime so the **new** files exist at those paths and are privately owned by the effective user. The block compares old references, changes exactly two rows in one transaction, and verifies all planning state is unchanged:

```sh
python - <<'PY'
import os
from pathlib import Path
from calendar_sync.state import StateStore
from calendar_sync.worker import WorkerSettings

settings = WorkerSettings.from_env(os.environ)
configuration_id = settings.configuration_id
def require(condition, message):
    if not condition:
        raise ValueError(message)
changes = (
    ('google', os.environ['OLD_GOOGLE_TOKEN_FILE'], settings.google_token_file),
    ('nextcloud', os.environ['OLD_NEXTCLOUD_PASSWORD_FILE'], settings.nextcloud_password_file),
)
for provider, old, new in changes:
    path = Path(new)
    require(path.is_absolute() and str(path.resolve()) == new and not path.is_symlink(), 'Invalid new path')
    info = path.stat()
    require(path.is_file() and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, 'Private file required')
with StateStore(settings.state_file) as store:
    before = store.planning_snapshot(configuration_id)
    require(before[0]['google_calendar_id'] == settings.google_calendar_id, 'Google calendar mismatch')
    require(before[0]['nextcloud_calendar_url'] == settings.nextcloud_calendar_url, 'Nextcloud calendar mismatch')
    with store.transaction() as db:
        for provider, old, new in changes:
            changed = db.execute('UPDATE credential_references SET file_path=? '
                'WHERE configuration_id=? AND provider=? AND file_path=?',
                (new, configuration_id, provider, old)).rowcount
            require(changed == 1, 'Old reference mismatch; transaction rolled back')
    require(store.planning_snapshot(configuration_id) == before, 'Unexpected planning-state change')
print('PASS: scoped credential references relocated; mappings preserved')
PY
```

Wrong old values/provider/configuration cause rollback; no configuration deletion/reset is prescribed. Other configurations are not updated. Then run one dry-run cycle with the same new settings. Keep restored volumes/paths stable. Google token replacement after workstation reauthorization is documented in [Docker UAT](docker-uat.md#renew-only-the-google-token--docker-uat-04), preserving state/Nextcloud credentials. Nextcloud app-password rotation likewise stops clients, privately stages a new valid file at the stable runtime path, verifies ownership/mode, then tests reads; revoke the old device token after the replacement is accepted. Never expose password values through command arguments or .env.

## Conflicts, lost state and retention — OPS-UAT-06

| Observation | Operator response |
| --- | --- |
| Existing destination lacks a confirmed mapping/journal | Preserve it; review lost-state collision. Matching title/UID/ownership property alone does not permit adoption. |
| Destination edited or mapped resource missing | Conflict; preserve previous mapping and user edit. Restoring the title does not restore the saved ETag. No automatic winner/reset command exists. |
| Uncertain/timed-out PUT | Preserve journal and same stable identities; later live execution reconciles before retrying. Dry-run does not confirm pending intents. |
| Source deleted/cancelled | Missing/unsupported review item; destination copy/mapping retained. No deletion path. |
| Recurring master/instance/exception | Skip explicitly; do not flatten. Check unsupported reasons/counts against expected fixtures. |
| Healthy empty/partial-looking preview | Validate the exact selected calendars and fixtures; complete API reads do not guarantee a cross-provider point-in-time snapshot. Never infer deletion. |
| One event fails | Unrelated events may have applied; inspect counts/audit privately and reuse state. No reset/recreate retry. |

Only supported ordinary timed/all-day fields are copied. Attendees/invitations, organizer identity, conferencing, reminders, attachments, privacy/availability and bidirectional edits are not reproduced. Choose destination access controls accordingly. No Keycloak/UI, Chat integration, ACL provisioning or recurrence support is implied by this runbook.

Audit entries are append-only and write intents are retained; **no retention purge/export command or automatic cleanup exists**. Do not schedule SQL DELETE, drop protective triggers, prune pending intents or vacuum away required recovery evidence. Keep application history until a separately reviewed retention migration/export policy exists. App history policy differs from backup/secret retention: assign an operator, choose encrypted independent backup storage and organization-appropriate retention/access, take backups before upgrades/relocations and on an agreed recovery-point schedule, and test restores. Do not invent a compliance period for this application.

Monitor last-cycle health, recurring/pending/conflict counts and state-volume growth/free space. Example aggregate disk check:

```sh
prod run --rm --no-deps worker python -c 'import shutil; usage=shutil.disk_usage("/data"); print("State volume free bytes:",usage.free)'
```

This does not open state/providers; it can run while the owning worker is active. Alert on persistent unhealthy/stale state and low space before writes fail. Docker unhealthy alone does not restart a process. Do not use global Docker prune or volume deletion as application retention/recovery.

## Acceptance and cleanup

OPS-UAT-01/02 require the selected account/runtime setup, credential permissions and an explicit preview-to-live decision. OPS-UAT-03/04 require a disposable populated state containing a mapping, run/audit records and a pending intent; backup and isolated restore preserve every table/trigger and attempts; active locks/existing targets/incompatible schema fail safely. OPS-UAT-05 requires synthetic two-configuration state and dummy private credential files: success changes only selected refs; a wrong second old path rolls the first change back. OPS-UAT-06 is documentation/negative-policy review plus existing conflict/no-delete/recurrence tests. Record each applicable case separately in [test-evidence.md](test-evidence.md).

Automated snapshot cases SNAP-001–014, documented relocation cases DOCOPS-001–002, state/journal/execution cases and template validation are separate from credentialed/production acceptance. Run `python -m unittest discover -s tests -p 'test_state_snapshot.py' -v` (14 passes) and `python -m unittest discover -s tests -p 'test_operations_docs.py' -v` (2 passes); the latter executes this guide's actual scoped-relocation block using synthetic two-configuration state and dummy private files, including second-update rollback and changed-calendar refusal. Both need installed dependencies on Linux/macOS, no credentials/provider access, and clean temporary state automatically. Exact full-suite/template commands and actual evidence appear in the PR; no production backup/restore, production authorization, remote conflict repair or destructive cleanup is claimed. For local disposable drills, remove only their temporary directories after closing all clients; never remove an active lock or real state. For UAT calendars/volumes follow [Docker UAT cleanup](docker-uat.md#results-cleanup-and-scope). Leave #14 release approval and #8 review-identity limitations explicit.
