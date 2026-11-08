# Dry-run planner and ownership checks

Issue #12 adds a pure planner and optional read-only provider preview. It never writes Google/Nextcloud events, deletes resources, adopts existing events, creates run/audit rows or advances applied mappings. It does not enable the worker or a live CLI mode. Default `calendar-sync` behavior remains offline; issue #13 adds the separate [executor library](execution.md).

## Unit tests and case mapping

From the repository root on Linux/macOS with Python 3.12+ and the virtual environment activated:

```sh
python -m pip install -e .
python -m unittest discover -s tests -p 'test_planner.py' -v
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
python -m pip check
```

Expected: 23 planner/preview tests, 141 total tests, 141 discovered test IDs documented once, and no broken dependencies. [PLAN-001–023](test-cases.md#plan-test_plannerpy) describe inputs/expected results. Tests use local dictionaries, generated iCalendar, fake readers and temporary SQLite. They require no credentials or provider access.

## Inputs and deterministic identity

`PlanContext(configuration_id, google_calendar_id, destination_calendar_url)` identifies one configuration and exact calendars. Use an explicit stable Google Calendar ID; `primary` is refused here because changing the token account could silently retarget that alias. The standalone Google adapter still supports it, but planner scopes must be stable. Destination URLs are canonicalized with the existing safe URL rules; HTTPS and local-loopback HTTP identities are representable. Representing an HTTP identity does not enable network HTTP: the CalDAV reader still requires its separate explicit local opt-in and matching origin.

`event_identity(context, source_id)` derives a SHA-256-based UID and `.ics` href from a versioned configuration/calendar owner hash and source ID. Titles, times, input ordering and source edits do not change identity. Different configurations/calendars/source IDs produce separate identities. Do not configure overlapping configurations expecting deduplication across them. Changing calendar configuration or identity algorithm requires deliberate migration/reconciliation, not automatic retargeting of existing mappings.

`build_plan(context, source_events, destination_resources, mappings, source_complete=True, destination_complete=True)` accepts complete materialized reader results and `MappingSnapshot` values. Completeness defaults to false; missing/nonboolean assertions fail closed. Never mark a partial or failed reader response complete. For real providers prefer `preview_sync`, which obtains complete results through their all-or-nothing read methods. Duplicate sources/resources/mappings, weak ETags, invalid scope/identity fields or cross-calendar resources reject the whole planning call.

## Action rules

| Action | Condition / reason examples | What it permits now |
| --- | --- | --- |
| create | Supported source with no saved mapping and no deterministic href/UID reservation/collision | A proposal only; no upload. |
| update | Saved deterministic mapping, supported destination, matching ETag, matching ownership markers and actual baseline content; source fingerprint changed | A proposal carrying the saved expected ETag; no write. |
| unchanged | All update preconditions pass and source fingerprint is unchanged | No action. |
| conflict | Missing/edited destination, mapping identity mismatch, marker/content mismatch, unsupported destination or unmapped identity collision | Skip/review; no adoption, overwrite or automatic repair. |
| unsupported | Invalid/special/recurring source or unmapped cancelled source | Skip with normalization reason and omitted field names where available. |
| missing | Saved source absent from a complete source read, or explicitly cancelled | Review only; no delete or cancellation propagation. |

There is no delete outcome. An empty complete source snapshot can generate missing notices, never deletion. Unknown/unowned destination resources are left untouched and do not become mappings. Similar titles, times or content are never used to match/adopt an existing event; an unrelated manual copy may coexist with a new deterministic create proposal. That proposal is not evidence that semantic duplicates are safe to create: review it before any future execution.

Plan ordering is stable by source identity/action/reason. `SyncPlan.counts()` returns only action counts. Plans/items/context reprs omit private data, but their actual fields contain source IDs, event content and destination identifiers: do not dump them into logs or public review evidence. Field omissions are retained in `PlanItem.omitted_fields`; this feature does not yet persist an audit explanation.

## Ownership and baseline validation

`serialize_owned_event(context, event, stamp=aware_datetime)` produces bytes without uploading them. It adds `X-TCADC-SYNC-OWNER`, `X-TCADC-SYNC-SOURCE` and `X-TCADC-SYNC-FINGERPRINT` to the single VEVENT. The first two contain hashes rather than raw configuration/calendar/source IDs; the third is the supported-field fingerprint. Ordinary conversion/serialization from issue #11 remains unchanged.

For a mapped event, the planner requires matching deterministic href/UID, an unchanged strong destination ETag, matching markers and a recomputed supported-field fingerprint equal to the saved applied source fingerprint. Unsupported extra event properties/components, scheduling METHOD, bad/missing/duplicate markers or corrupted payloads conflict. Benign CREATED/LAST-MODIFIED/SEQUENCE metadata and timezone definitions are allowed, but cannot override the fingerprint/ETag checks.

Markers are not cryptographic signatures or authorization. They are insufficient without a saved configuration-scoped mapping and matching baseline/ETag. After database loss, or a remote create that happened before a mapping was committed, the existing resource conflicts even if its markers look valid. No automatic adoption/recovery is implemented here. Preserve the data and use the future explicit reconciliation flow. Do not delete remote events simply to make a create plan succeed.

Plans are stale observations, not write capabilities. The executor validates ownership again, uses `If-None-Match: *` for creates and `If-Match` with a freshly verified baseline for updates, and reconciles uncertain results before retrying. It builds its own fresh plan; never execute based only on a saved plan's desired event or UID.

## Read-only SQLite/provider preview

`StateStore.planning_snapshot(configuration_id)` reads selected calendar fields and that configuration's applied mappings in one transaction. It excludes credential references and other configurations. `preview_sync(store, configuration_id, google, caldav)` checks reader calendar IDs/URL against the configuration before reading. It collects complete Google and CalDAV results and calls the pure planner. Known adapter errors retain their safe codes; unexpected reader exceptions become `planner_provider_read_failed`. Failure returns no partial plan and changes no mappings.

The preview creates no run/audit records and never calls `record_applied`. Opening an uninitialized StateStore still creates/migrates its database, and deliberately creating a configuration writes setup metadata. Google reads may legitimately refresh the token file using the existing secure refresh mechanism. Therefore preview is free of event/applied-state writes, not a promise of zero filesystem or server read-audit activity. Callers must authorize configuration access; SQLite scoping is not a future UI authorization layer.

## Acceptance cases

Use [the evidence template](test-evidence.md). PLAN-UAT-01–03 are credential-free. PLAN-UAT-04 needs separately authorized disposable Google/Nextcloud calendars. Live preview is Not run by the author; unit tests and a merge do not establish live acceptance.

| ID | Steps / prerequisites | Expected | Automated mapping |
| --- | --- | --- | --- |
| PLAN-UAT-01 | Run the pure scenario block below | Deterministic repeated create and unchanged baseline proposals | PLAN-001–004 |
| PLAN-UAT-02 | Same block exercises source edit, destination edit, source removal/cancellation, recurrence and lost mapping | update/conflict/missing/unsupported; no delete or adoption | PLAN-005–019 |
| PLAN-UAT-03 | Run temporary SQLite/fake-reader block | Repeated preview creates no applied mappings/run/audit rows | PLAN-020–023 |
| PLAN-UAT-04 | Complete prior Google/Nextcloud setup and run live-read block | Stable proposal counts across unchanged readers; no provider writes or applied rows | PLAN-020–023 with fakes; real provider/config compatibility manual |

### PLAN-UAT-01 and PLAN-UAT-02: pure scenarios

Copy the complete shell block. Its saved mapping/resource is a synthetic in-memory fixture, not a claim that a real event was applied. No files, credentials or provider clients are used.

```sh
python - <<'PY'
from dataclasses import replace
from datetime import datetime, timezone
from calendar_sync.caldav import CalendarResource
from calendar_sync.events import normalize_google_event
from calendar_sync.planner import PlanContext, MappingSnapshot, build_plan, event_identity, serialize_owned_event

context = PlanContext('uat', 'master@example.invalid', 'https://nextcloud.tcadc.com/remote.php/dav/calendars/test/uat/')
source = {'id': 'example', 'summary': 'Synthetic test',
          'start': {'dateTime': '2026-11-08T09:00:00Z'}, 'end': {'dateTime': '2026-11-08T10:00:00Z'}}
event = normalize_google_event(source).event
identity = event_identity(context, event.source_id)
mapping = MappingSnapshot('uat', event.source_id, identity.href, identity.uid, event.fingerprint(), '"baseline"')
resource = CalendarResource(identity.href, '"baseline"', identity.uid,
    serialize_owned_event(context, event, stamp=datetime(2026, 11, 7, tzinfo=timezone.utc)), None)

def plan(sources, resources=(), mappings=()):
    return build_plan(context, sources, resources, mappings, source_complete=True, destination_complete=True)

first = plan([source])
assert first == plan([source]) and first.counts()['create'] == 1
cases = [
    (plan([source], [resource], [mapping]), 'unchanged'),
    (plan([{**source, 'summary': 'Edited'}], [resource], [mapping]), 'update'),
    (plan([source], [replace(resource, etag='"edited"')], [mapping]), 'conflict'),
    (plan([], [resource], [mapping]), 'missing'),
    (plan([{'id': 'example', 'status': 'cancelled'}], [resource], [mapping]), 'missing'),
    (plan([{**source, 'recurrence': ['RRULE:FREQ=DAILY']}], [resource], [mapping]), 'unsupported'),
    (plan([source], [resource]), 'conflict'),
]
for result, expected in cases:
    assert result.dry_run and len(result.items) == 1 and result.items[0].action == expected
    assert 'delete' not in result.counts()
print('PASS: create/unchanged/update/conflict/missing/unsupported; lost mapping not adopted; no writes')
PY
```

Expected exit 0 and PASS. Review that the lost-mapping case conflicts rather than recreating/adopting the resource. Cleanup: none.

### PLAN-UAT-03: temporary state, fake readers

```sh
python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from calendar_sync.planner import preview_sync
from calendar_sync.state import StateStore

url = 'https://nextcloud.tcadc.com/remote.php/dav/calendars/test/uat/'
source = {'id': 'example', 'start': {'date': '2026-11-08'}, 'end': {'date': '2026-11-09'}}
google = SimpleNamespace(calendar_id='master@example.invalid', list_events=lambda: [source])
caldav = SimpleNamespace(calendar_url=url, list_resources=lambda: [])
with TemporaryDirectory() as directory:
    with StateStore(Path(directory) / 'preview.sqlite3') as store:
        store.create_configuration('uat', 'operator', google.calendar_id, url,
                                   google_credential_file='/unused-google', nextcloud_credential_file='/unused-nextcloud')
        before = store.planning_snapshot('uat')
        first = preview_sync(store, 'uat', google, caldav)
        second = preview_sync(store, 'uat', google, caldav)
        assert first == second and first.counts()['create'] == 1
        assert store.planning_snapshot('uat') == before
        for table in ('runs', 'audit_entries', 'mappings'):
            assert store.connection.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
print('PASS: repeatable dry-run preview; no applied mappings, runs or audit rows')
PY
```

Expected exit 0 and PASS; temporary metadata/database/lock files are cleaned automatically. This does not initialize production state or read secret files.

### PLAN-UAT-04: optional live reads using disposable calendars

Prerequisites: complete the applicable [Google account/setup path](google-setup.md) and [Google fixture UAT](google-acceptance.md); complete [Nextcloud setup/read UAT](nextcloud.md). Both accounts must be authorized for their exact calendars. Confirm fixtures in the UI even if earlier PRs merged without full acceptance. Export the existing `GOOGLE_UAT_TOKEN_FILE`, exact stable `GOOGLE_UAT_CALENDAR_ID`, `NEXTCLOUD_UAT_CALENDAR_URL`, `NEXTCLOUD_UAT_USERNAME`, `NEXTCLOUD_UAT_PASSWORD_FILE`. For local HTTP, use the explicit origin/flag from NC-UAT-06; leave production HTTPS defaults unchanged.

```sh
python - <<'PY'
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from calendar_sync.caldav import CalDAVCalendar, CalDAVError
from calendar_sync.google_calendar import GoogleCalendar
from calendar_sync.google_oauth import GoogleError
from calendar_sync.planner import PlanningError, preview_sync
from calendar_sync.state import StateStore

mode = os.environ.get('NEXTCLOUD_UAT_ALLOW_HTTP_LOOPBACK', 'false')
if mode not in {'true', 'false'}:
    raise SystemExit('NEXTCLOUD_UAT_ALLOW_HTTP_LOOPBACK must be true or false')
try:
    google = GoogleCalendar(os.environ['GOOGLE_UAT_TOKEN_FILE'], os.environ['GOOGLE_UAT_CALENDAR_ID'])
    caldav = CalDAVCalendar(os.environ['NEXTCLOUD_UAT_CALENDAR_URL'], os.environ['NEXTCLOUD_UAT_USERNAME'],
        os.environ['NEXTCLOUD_UAT_PASSWORD_FILE'],
        origin_url=os.environ.get('NEXTCLOUD_UAT_ORIGIN_URL', 'https://nextcloud.tcadc.com'),
        allow_http_loopback=mode == 'true')
    with TemporaryDirectory() as directory:
        with StateStore(Path(directory) / 'preview.sqlite3') as store:
            store.create_configuration('uat-preview', 'operator', google.calendar_id, caldav.calendar_url,
                google_credential_file=str(Path(os.environ['GOOGLE_UAT_TOKEN_FILE']).resolve()),
                nextcloud_credential_file=str(Path(os.environ['NEXTCLOUD_UAT_PASSWORD_FILE']).resolve()))
            before = store.planning_snapshot('uat-preview')
            first = preview_sync(store, 'uat-preview', google, caldav)
            second = preview_sync(store, 'uat-preview', google, caldav)
            assert first.counts() == second.counts(), 'FAIL: proposal counts changed between reads'
            assert store.planning_snapshot('uat-preview') == before
            for table in ('runs', 'audit_entries', 'mappings'):
                assert store.connection.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
            print('Dry-run action counts:', first.counts())
            print('PASS: complete read-only preview; no provider writes or applied-state changes')
except (GoogleError, CalDAVError, PlanningError, ValueError) as error:
    raise SystemExit(str(error)) from None
PY
```

Expected exit 0, stable counts and PASS when readers/scopes are valid. With one supported timed event, one supported all-day event and one recurring master, an empty scratch mapping table normally yields two create proposals and at least one unsupported notice. Returned Google cancellation history adds unsupported notices. Counts are not fixed to three records. Existing manual Nextcloud fixtures are unowned and are not matched by title; they remain unchanged while supported sources receive their own deterministic proposals. A deterministic identity collision instead yields conflict. Do not upload these proposals or seed applied mappings to make the counts change.

Verify calendars in both UIs remain unchanged. Share only counts/fixed codes, not event bodies or IDs. Failure from either read returns no partial plan; run the corresponding provider-only UAT for its diagnostic code. Changing calendars between reads can legitimately change counts; resolve fixture changes before recording PASS. Temporary state is removed automatically. Do not revoke reusable UAT grants until all related cases finish; follow each provider guide's disposable-resource cleanup afterward. No production database is opened by this script.

## Limits and next steps

Snapshots are not simultaneous/provider transactions, and plans are not a lock on remote events. No applied mappings are created in this feature, so a real update/unchanged baseline cannot yet be established by end-to-end sync; synthetic PLAN-UAT-02 demonstrates those branches without pretending a write occurred. Plan/audit persistence, approved conditional execution, uncertain-create recovery and periodic operation are future work. Preserve ownership conflicts for explicit review; never weaken them because a title or marker looks familiar.
