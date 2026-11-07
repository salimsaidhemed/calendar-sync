# Testing Google OAuth and calendar reads

Review instructions for issue #9 and PR #24. Run from the repository root on Linux or macOS with Python 3.12+; use the PR branch (`gh pr checkout 24`) when reviewing before merge. Do not use production tokens or calendars for manual acceptance testing. No test here exercises Nextcloud writes or an end-to-end sync engine, which are not implemented yet.

## Unit tests: no credentials or provider access

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -p 'test_google_*.py' -v
python -m unittest discover -s tests -v
python -m pip check
```

Expected on this PR: 27 focused Google tests and 45 full-suite tests pass with `OK`; dependency checks report no broken requirements. Package installation needs access to the package registry. Tests themselves use fake authorization flows, tokens and HTTP responses; no Google account, browser or network is needed. They cover refresh/revocation, offline grants, scope/endpoint validation, permissions, token replacement/locking, complete pagination, retries, malformed/partial reads and diagnostic redaction. CI repeats the full suite on Python 3.12 and builds/smokes the Docker image.

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

Prerequisites: browser on the same workstation, network access, Google Calendar API enabled, a Desktop OAuth client approved for the dedicated test account, and a private `secrets/` directory. Follow [Google setup](google.md#one-time-authorization-on-your-workstation) using `secrets/google-uat-client.json` and `secrets/google-uat-token.json` as the client/token paths. Neither file is committed or included in the Docker image.

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

Prerequisites: UAT C passes. In the dedicated test account create a new, otherwise empty calendar and these three events using Google Calendar's UI:

- A single timed event titled `Calendar Sync UAT timed`.
- A single all-day event titled `Calendar Sync UAT all-day`.
- A daily recurring event titled `Calendar Sync UAT recurring`.

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

For the PR, record reviewer, date, tested commit, runtime/environment and passed/failed/not-run status for UAT A–E (including subcases), with reasons for skipped cases. Separate unit/CI evidence from manual acceptance. Submit only sanitized output and counts, never event bodies, account identifiers or credentials. Owner review and merge remain required.
