# Conditional execution and recovery

Issue #13 adds a library entry point, `calendar_sync.executor.execute_sync(store, configuration_id, google, caldav)`. It returns a dry-run report by default. `report.plan.counts()` describes proposals; `report.counts()` describes execution outcomes and is empty/zero in dry-run. No run, audit, mapping or intent is recorded by dry-run, including when recovery is pending. The bare CLI/image remains offline; [the worker](worker.md) explicitly enables polling and Compose integration.

Pass the literal keyword `dry_run=False` to enable Nextcloud writes. Google remains read-only. Complete provider reads, explicit matching calendar identities and deterministic destination identities are required. Creates use `If-None-Match: *`; updates revalidate actual content/ownership and the saved ETag, then send `If-Match`. HTTP 409/412 conflicts never cause unconditional retries. See [HTTP conditional semantics](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1) and [CalDAV resource creation](https://www.rfc-editor.org/rfc/rfc4791.html#section-5.3.2).

## Durable operation protocol

1. Build a fresh complete plan. Invalid/partial reads stop before a new execution run or PUT.
2. For live execution only, interrupt prior unfinished execution runs for this configuration and open a new run. Reconcile pending intents before planning new writes. Manual runs and other configurations are untouched.
3. Save an immutable write intent in SQLite before network mutation. It records an operation UUID, deterministic URL/UID, desired fingerprint, payload hash, fixed serialization timestamp and expected baseline. It contains no event body or credential contents.
4. GET the exact scoped URL. An existing resource is accepted as a completed operation only when ownership, source identity, real normalized content, desired fingerprint, strong ETag and that operation's UUID all match a previously attempted intent. A bare matching UID or ownership marker never permits adoption.
5. Before an actual PUT, re-read the complete Google calendar and normalize the source again. Changed, cancelled, missing or unsupported sources conflict; failed/ambiguous reads leave the intent pending. Reconstructed bytes must match the saved payload hash. Persist the attempt increment, then send one conditional PUT.
6. Read back the destination even if the PUT times out or its response is lost (rate-limited responses defer readback to a later cycle). Only verified content/operation/ETag permits one atomic transaction completing the mapping, applied audit and journal. Otherwise retain pending state or record a conflict, preserving the previous mapping.

At most one PUT per intent per invocation; default total budget is three persisted attempts, configurable through `max_put_attempts=1..10`. Exhausted intents remain pending and still allow readback recovery; raising the limit is an explicit operator decision. There is no blind sleep/retry loop for writes. Network failures affect that event; unrelated events may still apply. Provider rate-limit hints pause remaining events until a later cycle. Conflict/pending outcomes make the run `failed`; skipped unsupported/missing inputs alone do not. Exceptions before/after a run are separately surfaced with fixed codes. Optional cooperative shutdown checks prevent starting another event/PUT, preserve an in-flight write confirmation and mark an interrupted run.

A crash after remote create/update but before local commit leaves a durable intent. Re-running live execution against the same database reconciles it without a duplicate PUT when readback matches. A crash before sending consumes an attempt conservatively. Dry-run cannot repair state. If the source changed while an already-applied operation was awaiting confirmation, recovery can save that confirmed older baseline; its next source update waits until a later invocation.

## Limits and operator decisions

Only Google-to-Nextcloud single events are supported. Recurring masters, instances and exceptions are skipped; recurrence is never flattened. Attendees/invitations, alarms, conferencing and bidirectional changes remain unsupported. Missing/cancelled sources never delete destination resources or mappings. No delete method exists.

Destination edits, missing mapped resources, unknown ownership, stripped operation properties or changed ETags require manual review. There is no automatic conflict winner, adoption, mapping reset or conflict-resolution UI. Journal rows and audit history are retained; retention/export policy remains future work. Preserve the SQLite database, configuration ID and exact calendar identities across restarts. A different configuration ID intentionally produces different identities and can create another set of copies. Never clear state to retry a failed production run; use [backup/recovery](state.md#backup-and-recovery).

Google has no cross-provider transaction or point-in-time list guarantee. The extra full source read per attempted write is conservative and potentially expensive; a source change after that read may be reflected only on the next invocation. Destination GET/PUT races are protected by HTTP preconditions, which the server must honor. Remote edits after verified readback cause a later ETag conflict. Tests simulate protocol behavior; real server interoperability requires live acceptance. No production deployment readiness is claimed.

## Automated checks

Prerequisites: repository root on Linux/macOS, Python 3.12+, installed dependencies as in [testing.md](testing.md). No credentials or network needed after installation.

```sh
python -m unittest discover -s tests -p 'test_executor.py' -v
python -m unittest discover -s tests -p 'test_journal.py' -v
python -m unittest discover -s tests -p 'test_caldav.py' -v
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
```

Expected: 30 executor, 6 journal, 31 CalDAV and 167 total tests pass; 167 tests documented exactly once. EXEC/JOURNAL/DAV rows in [the catalogue](test-cases.md) document every scenario and subcase. Temporary databases/fake calendars are automatically cleaned up. No live calendar is modified by these tests.

## Acceptance cases

Record each result with tested commit/environment and [the evidence template](test-evidence.md). Unit tests do not establish live UAT. Current implementation evidence: automated checks run separately; **all live cases below are Not run by the agent** (no credentials or calendar writes used).

| Case | Procedure / expected result | Automated coverage |
| --- | --- | --- |
| EXEC-UAT-01 | Run automated commands above and inspect default report test: dry-run proposes one create with zero state/write effects. Offline test evidence only. | EXEC-001–002, EXEC-016 |
| EXEC-UAT-02 | Follow disposable setup and preview below. Exactly two create proposals; zero applied mappings, no Nextcloud events. | EXEC-001–002 |
| EXEC-UAT-03 | Explicitly enable test writes and run once: applied=2, mapping count=2, two matching events in only the dedicated Nextcloud calendar. Run again: unchanged=2, applied=0, still two events. | EXEC-002, DAV-027 |
| EXEC-UAT-04 | Edit the Google timed fixture title to `EXEC timed edited`, save and rerun live. applied=1, unchanged=1; same destination UID/URL, edited title visible; total remains two. | EXEC-003 |
| EXEC-UAT-05 | Edit that destination title in Nextcloud to `EXEC destination edited`; also edit its Google title. Rerun live: conflict=1, unchanged=1, status failed; Nextcloud edit and previous mapping fingerprint/ETag remain unchanged. | EXEC-004–005, EXEC-013 |
| EXEC-UAT-06 | Delete the Google all-day fixture through its UI and rerun live. Missing/cancelled source is skipped; its Nextcloud copy and mapping remain; timed conflict may remain. No deletion attempted. | EXEC-017 |
| EXEC-UAT-07 | Run crash/retry/race tests below against fakes; inspect one remote resource/PUT across recovery and failed finalization. Do not induce crashes against real calendars. Offline recovery evidence only. | EXEC-008–015, EXEC-019–020, JOURNAL-001–006 |

### Disposable live setup (EXEC-UAT-02 through 06)

Use only dedicated test accounts/calendars. Complete the appropriate personal/Workspace **testing** authorization path in [google-setup.md](google-setup.md), with a read-only Google event token. Production setup paths are Not applicable to these disposable cases; production readiness requires separate evidence. Enable Google Calendar API, test-user/audience permissions and calendar read access first.

Create a new Google secondary calendar named `Calendar Sync EXEC UAT`, record its **exact calendar ID** (not `primary`), and create exactly two events assigned to it:

- `EXEC timed`: event (not task), 8 November 2026, 09:00–10:00, timezone UTC, **All day OFF**, **Does not repeat**, no guests/reminders.
- `EXEC all-day`: event, 8 November 2026, **All day ON**, one calendar day (exclusive API end 9 November), **Does not repeat**, no guests/reminders.

Create an empty Nextcloud calendar named `Calendar Sync EXEC UAT`, separate from earlier NC-UAT fixtures and personal calendars. Copy that calendar's actual CalDAV URL from its calendar menu; the web app URL is not a CalDAV URL. The app password must allow reading and writing that calendar. Do not put credentials in URLs or terminal commands. Reuse the permission-restricted token/app-password files described in the Google/Nextcloud guides. Use one stable, private local state path for every invocation; never use a temporary directory for live execution.

Export the variables below, replacing placeholders locally. None is a password. The directory will be created privately if missing; protect any existing directory yourself.

```sh
export GOOGLE_UAT_TOKEN_FILE='/absolute/private/path/google-token.json'
export GOOGLE_UAT_CALENDAR_ID='EXACT_DISPOSABLE_GOOGLE_CALENDAR_ID'
export NEXTCLOUD_UAT_CALENDAR_URL='https://nextcloud.tcadc.com/remote.php/dav/calendars/TEST_USER/EXACT_EXEC_CALENDAR/'
export NEXTCLOUD_UAT_ORIGIN_URL='https://nextcloud.tcadc.com'
export NEXTCLOUD_UAT_USERNAME='TEST_USER'
export NEXTCLOUD_UAT_PASSWORD_FILE='/absolute/private/path/nextcloud-app-password'
export NEXTCLOUD_UAT_ALLOW_HTTP_LOOPBACK='false'
export EXEC_UAT_STATE_FILE="$PWD/.local/exec-uat/state.sqlite3"
export EXECUTE_TEST_WRITES='false'
```

For local Nextcloud testing only, replace the calendar URL with its real `http://localhost:8090/remote.php/dav/calendars/.../` URL, set origin to `http://localhost:8090` and loopback flag to `true`. All three must agree. See [loopback setup](nextcloud.md#nc-uat-06-optional-local-http-testing). No HTTP non-loopback host or TLS verification bypass is allowed. Do not switch calendars in an existing state configuration.

Copy the **whole** block into the activated project shell for each run:

```sh
python - <<'PY'
import os
from pathlib import Path
from calendar_sync.caldav import CalDAVCalendar
from calendar_sync.google_calendar import GoogleCalendar
from calendar_sync.executor import execute_sync
from calendar_sync.state import StateStore

write = os.environ.get('EXECUTE_TEST_WRITES', 'false')
loopback = os.environ.get('NEXTCLOUD_UAT_ALLOW_HTTP_LOOPBACK', 'false')
if write not in {'true', 'false'} or loopback not in {'true', 'false'}:
    raise SystemExit('Boolean options must be true or false')
google = GoogleCalendar(os.environ['GOOGLE_UAT_TOKEN_FILE'], os.environ['GOOGLE_UAT_CALENDAR_ID'])
caldav = CalDAVCalendar(os.environ['NEXTCLOUD_UAT_CALENDAR_URL'], os.environ['NEXTCLOUD_UAT_USERNAME'],
    os.environ['NEXTCLOUD_UAT_PASSWORD_FILE'], origin_url=os.environ['NEXTCLOUD_UAT_ORIGIN_URL'],
    allow_http_loopback=loopback == 'true')
with StateStore(Path(os.environ['EXEC_UAT_STATE_FILE']).resolve()) as store:
    exists = store.connection.execute('SELECT 1 FROM configurations WHERE id=?', ('exec-uat',)).fetchone()
    if not exists:
        store.create_configuration('exec-uat', 'uat-operator', google.calendar_id, caldav.calendar_url,
            google_credential_file=str(Path(os.environ['GOOGLE_UAT_TOKEN_FILE']).resolve()),
            nextcloud_credential_file=str(Path(os.environ['NEXTCLOUD_UAT_PASSWORD_FILE']).resolve()))
    report = execute_sync(store, 'exec-uat', google, caldav, dry_run=write != 'true')
    print('Mode:', report.status)
    print('Plan:', report.plan.counts())
    print('Outcomes:', report.counts())
    print('Mappings:', store.connection.execute('SELECT count(*) FROM mappings WHERE configuration_id=?',
                                               ('exec-uat',)).fetchone()[0])
    print('Reasons:', sorted({item.reason for item in report.items}))
PY
```

EXEC-UAT-02: with writes false expect Mode dry_run, plan create=2 and all other proposals zero; outcome counts all zero, mappings=0. Inspect only the new destination calendar: still empty. If proposals differ, stop and check exact selected calendars/fixtures before proceeding. Configuration creation itself persists a configuration, but dry-run records no execution history or applied state.

EXEC-UAT-03: after checking the preview, run `export EXECUTE_TEST_WRITES='true'`, then repeat the entire block. Expect Mode succeeded, outcomes applied=2 and all others zero, mappings=2. Repeat once more: unchanged=2, applied=0. Inspect both events' dates/types and the destination event count in the Nextcloud UI. Complete cases 04–06 in table order, repeating this same block after each saved UI change. No calendar write permission is needed on Google by this application.

### Offline crash and race evidence (EXEC-UAT-07)

```sh
python -m unittest discover -s tests -p 'test_executor.py' -k crash -v
python -m unittest discover -s tests -p 'test_executor.py' -k atomic_finalize -v
python -m unittest discover -s tests -p 'test_executor.py' -k race -v
python -m unittest discover -s tests -p 'test_journal.py' -v
```

Expected: respectively 3, 1, 2 and 6 tests pass, with automatic cleanup. They simulate abrupt interruption, reopen SQLite and reconcile fake remote state; they never contact real providers. Live timeout/crash injection is Not applicable to these safe manual procedures; real interoperability remains unaccepted until cases 02–06 are run.

### Failure handling and cleanup

For authorization/API/HTTP failures follow [Google troubleshooting](google-troubleshooting.md) and [Nextcloud setup](nextcloud.md). Fixed reason codes identify skipped/conflicted/pending writes; inspect counts and private audit metadata, never paste event bodies/tokens. A failed run may contain successfully applied events; rerun with the same state rather than resetting it. No automatic conflict-resolution procedure is provided.

When all clients are stopped, disable test writes (`export EXECUTE_TEST_WRITES='false'`). Manually remove **only** the two dedicated EXEC UAT calendars through their respective UIs after recording evidence. Archive the dedicated test database privately if keeping audit evidence, or remove only that test directory after confirming the path; never delete production state or an active lock. Revoke only a dedicated test app password/token if no longer needed. Do not clean up the fixtures from other UAT guides or personal calendars.
