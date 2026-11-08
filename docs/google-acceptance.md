# Testing Google OAuth and calendar reads

These cases cover the Google OAuth/read feature introduced by issue #9. Run from the repository root on Linux or macOS with Python 3.12+, checking out the PR under review rather than a historical merged branch. First follow the applicable personal/Workspace test path in [Google setup](google-setup.md). Use disposable UAT resources, not production tokens/calendars. No case here tests Nextcloud writes or a complete sync engine.

## Manual case matrix

Each row has shared environment requirements above plus the prerequisites in its linked procedure. Run the complete command blocks, including Python heredoc delimiters. Record a result for each row/subcase using [the evidence template](test-evidence.md); merged PRs do not imply all rows passed.

| Case | Procedure / input | Observable expected result | Related automated cases (not a replacement for live UAT) |
| --- | --- | --- | --- |
| UAT-01 | A.1: default CLI, no credentials | Exit 0; explicitly offline/not implemented; no files/browser | CFG-005 |
| UAT-02 | A.2: explicit live flag | Exit 2, live sync unavailable; no provider writes | CFG-004 |
| UAT-03 | A.3: authorization help | Exit 0; file options shown; no browser | Manual CLI-help check |
| UAT-04 | B: absent token in temporary directory | PASS with authorization-required code; no browser/network | OAUTH-005 |
| UAT-05 | C success: dedicated account + valid Desktop client | Consent completes; exit 0; token 0600, parent 0700; no secret output | OAUTH-001 |
| UAT-06 | C denial: cancel/deny a second authorization | Fixed error/nonzero exit; old token remains; no raw error | OAUTH-010 simulates flow failure; real denial is manual |
| UAT-07 | D: exact timed/all-day/daily fixtures | Three active records with correct start types and recurring master; PASS | GCAL-001 covers markers; real event-type setup is manual |
| UAT-08 | D repeated read + Calendar UI comparison | Same three active fixtures, no duplicates or event changes | GCAL-001, GCAL-003; live comparison manual |
| UAT-09 | D with deleted fixture history if present | Cancelled records excluded only by UAT assertions; three active fixtures pass | GCAL-001 preserves cancellation records |
| UAT-10 | E.1: read after test access token expires | Successful read without browser; refreshed token remains 0600 | OAUTH-003; delayed live check recorded separately |
| UAT-11 | E.2: disposable token made 0644 | Nonzero exit and google_secret_permissions; no event output | OAUTH-007 |
| UAT-12 | E.2 recovery: restore disposable token 0600 | Read succeeds again, no browser | OAUTH-007 covers rejection; live recovery manual |

For UAT-09, do not delete valid fixtures solely to manufacture history. If no cancelled history exists, record Not applicable with that reason; the fake cancellation test still runs. For UAT-10, record Not run if expiry has not occurred, rather than marking Passed based on its unit test. Error-code diagnosis uses [the troubleshooting guide](google-troubleshooting.md) and GCAL-007 through GCAL-009. Cleanup for every live row is in the final section; no cleanup may revoke production grants.

## Unit tests: no credentials or provider access

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -p 'test_google_*.py' -v
python -m unittest discover -s tests -v
python -m pip check
```

Expected in this revision: 28 focused Google tests and 167 full-suite tests pass with `OK`; dependency checks report no broken requirements. See [the case catalogue](test-cases.md) for each case's inputs and expected assertions, and run `python scripts/check_test_catalogue.py` to detect undocumented tests. Installation needs registry access; tests use fakes without real credentials or Google requests. CI repeats the full suite on Python 3.12 and builds/smokes the Docker image.

## UAT A: offline default and refusal of live sync

Prerequisites: installation above; no credentials, Docker or test events required.

1. Run `calendar-sync`. Expect exit 0 and `Calendar Sync scaffold: dry-run enabled; no providers contacted; sync not implemented.` No browser opens and no database or credential file is created.
2. Run `SYNC_DRY_RUN=false calendar-sync`. Expect exit 2 and `Live sync is unavailable in this scaffold.` No browser opens and no calendar changes occur. This deliberate failure should not be treated as a failed acceptance test.
3. Run `calendar-sync authorize-google --help`. Expect exit 0 and help showing required `--client-file` and `--token-file` options, with no authorization attempt.

Cleanup: none. Record each command's exit status (`echo $?` immediately afterward) and sanitized output.

## UAT B: missing authorization fails without browser login

Prerequisites: installation above; no Google credentials or network required. This script uses a temporary private directory and intentionally has no token file.

```sh
python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from calendar_sync.google_calendar import GoogleCalendar
from calendar_sync.google_oauth import GoogleError

with TemporaryDirectory() as directory:
    calendar = GoogleCalendar(Path(directory) / 'missing-token.json', 'unused@example.invalid')
    try:
        calendar.list_events()
    except GoogleError as error:
        if str(error) != 'google_authorization_required':
            raise SystemExit('FAIL: unexpected error code')
        print('PASS: google_authorization_required; no calendar read performed')
    else:
        raise SystemExit('FAIL: missing authorization was accepted')
PY
```

Expected: script exits 0 with the PASS line, no browser opens and no Google request occurs. Cleanup is automatic, including the temporary lock file.

## UAT C: local authorization with a disposable test account

Prerequisites: browser on the same workstation, network access, Google Calendar API enabled in the client project's API library, a Desktop OAuth client accessible to the exact dedicated test account, and a private `secrets/` directory. Complete [the relevant personal/Workspace setup path](google-setup.md) first. External Testing needs an explicit test-user entry; Workspace may require administrator access approval. Use `secrets/google-uat-client.json` and `secrets/google-uat-token.json` consistently. Neither file is committed or included in the Docker image.

```sh
mkdir -p secrets
chmod 700 secrets
# Place the test Desktop client JSON at secrets/google-uat-client.json first.
chmod 600 secrets/google-uat-client.json
calendar-sync authorize-google \
  --client-file "$PWD/secrets/google-uat-client.json" \
  --token-file "$PWD/secrets/google-uat-token.json"
```

Expected: browser consent for read-only events, a successful loopback return, exit 0, and `Google authorization saved. No calendar events were modified.` The token file exists with mode 0600 and the directory has mode 0700. Do not open or paste the token contents in review evidence. The terminal must not contain authorization URLs, codes, access tokens or refresh tokens.

Failure scenario: rerun the command and deny/cancel consent. Expect a nonzero exit with a fixed `google_*` reason code (up to the 180-second browser wait if cancelled without redirect), no raw upstream payload or traceback, and the previous token file unchanged. Cancellation does not itself revoke the prior grant. Restore successful test-account authorization if needed before UAT D.

## UAT D: read the selected disposable calendar

Prerequisites: UAT C passes and terminal reports the token was saved. Create/select a dedicated otherwise empty `Calendar Sync UAT` calendar using [the calendar setup steps](google-setup.md#create-and-select-the-calendar). Choose a future date and an explicit timezone (for example Europe/Madrid). Use Google Calendar's event editor to create each row exactly once:

| Exact title | Calendar field | Required editor controls | Save / verify |
| --- | --- | --- | --- |
| `Calendar Sync UAT timed` | Calendar Sync UAT | All day OFF; 09:00–10:00 on selected date; Does not repeat | Save; open again and confirm visible time fields. |
| `Calendar Sync UAT all-day` | Calendar Sync UAT | All day ON; selected single date; Does not repeat | Save; reopen and confirm All day remains checked and no timed start is selected. |
| `Calendar Sync UAT recurring` | Calendar Sync UAT | All day OFF; 11:00–12:00; repeat Daily, optionally end after three occurrences | Save the series; reopen and confirm Daily repeat. Only the master is expected in this unexpanded API read. |

The title `all-day` does not create an all-day event: the editor's All day control must be enabled. Do not create a second set of events on retry. Before reading, verify the selected UAT calendar contains exactly these three active fixtures; delete only accidental test duplicates (the whole duplicate recurring series), or edit their fields. Cancelled history is allowed and must not be removed. Creating recurring instances/overrides manually is outside this fixture.

Copy that calendar's exact ID from its settings. Set the variables below locally, replacing the placeholder; these values are not automatically loaded from `.env`.

```sh
export GOOGLE_UAT_CALENDAR_ID='replace-with-the-disposable-calendar-id'
export GOOGLE_UAT_TOKEN_FILE="$PWD/secrets/google-uat-token.json"
python - <<'PY'
import os
from calendar_sync.google_calendar import GoogleCalendar
from calendar_sync.google_oauth import GoogleError

calendar = GoogleCalendar(os.environ['GOOGLE_UAT_TOKEN_FILE'], os.environ['GOOGLE_UAT_CALENDAR_ID'])
try:
    events = calendar.list_events()
except GoogleError as error:
    raise SystemExit(str(error)) from None
events = [event for event in events if event.get('status') != 'cancelled']
by_title = {event.get('summary'): event for event in events}
expected = {'Calendar Sync UAT timed', 'Calendar Sync UAT all-day', 'Calendar Sync UAT recurring'}
assert set(by_title) == expected and len(events) == 3, 'FAIL: unexpected test fixture count/titles'
assert 'dateTime' in by_title['Calendar Sync UAT timed']['start'], 'FAIL: timed event missing'
assert 'date' in by_title['Calendar Sync UAT all-day']['start'], 'FAIL: all-day event missing'
assert by_title['Calendar Sync UAT recurring'].get('recurrence'), 'FAIL: recurring master missing'
assert not any(event.get('recurringEventId') for event in events), 'FAIL: series was expanded'
print('PASS: all three test events read; recurring master preserved; no writes performed')
PY
```

Expected: exit 0 with the PASS line. Verify in the Calendar UI that the three events remain unchanged. Repeat the script: expect the same count and no duplicates or new events in the UI. The read adapter must not open a browser. If event edits are still propagating, wait and retry before diagnosing a fixture mismatch.

The fixture assertions count only active events. Deleted test events can remain as cancellation records because the adapter intentionally requests `showDeleted=true`; they are excluded only by this UAT script, not by the adapter. For example, six returned records with three cancelled and one active event of each expected title is a valid fixture. Duplicate active events still fail the count check. Do not recreate calendars merely to remove cancellation history.

Full pagination, rate limits and partial-response failures are covered by the fake-response tests; this three-event manual fixture does not establish live pagination behavior. The future planner will skip recurrence; this adapter merely preserves the recurring master.

## UAT E: token refresh and rejected secret permissions

Prerequisites: the same disposable test grant; keep all clients using the test token stopped when changing permissions.

1. Once the test access token expires, repeat UAT D without running authorization again. Expect a successful read, no browser and mode 0600 on the refreshed token file. Do not force expiry by editing a production token. Refresh and revoked-token behavior are covered immediately by unit tests; mark this manual refresh scenario not run if you cannot wait for expiry.
2. Run `chmod 644 secrets/google-uat-token.json`, then repeat UAT D. Expect nonzero exit with `google_secret_permissions`, no event output and no browser. Restore with `chmod 600 secrets/google-uat-token.json` and repeat UAT D; expect success. Restore permissions even if another check fails.

Never paste token JSON into UAT evidence. These checks confirm only the disposable account/calendar integration, not unattended worker operation or end-to-end sync.

## Cleanup and acceptance record

After all clients stop, revoke the dedicated test app grant from the test account's Google permissions if it is no longer needed. Remove only the disposable calendar/events and test secret/lock files you created. Keep production grants and calendars unchanged. Run `unset GOOGLE_UAT_CALENDAR_ID GOOGLE_UAT_TOKEN_FILE` in the test shell; deactivate the virtual environment if desired.

For the PR, record reviewer, date, tested commit, runtime/environment, selected SETUP path and a status for each UAT-01 through UAT-12, with reasons for skipped/not-applicable cases. Use [the evidence template](test-evidence.md). Separate unit/CI evidence from manual acceptance; do not infer an event-shape PASS from count-only diagnostics. Submit only sanitized output/counts, never event bodies, account identifiers or credentials. Owner review and merge remain required.
