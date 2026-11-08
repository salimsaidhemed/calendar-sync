# Nextcloud CalDAV reads and event conversion

Issue #11 provides explicit-calendar CalDAV reads at `https://nextcloud.tcadc.com`, plus pure event normalization/serialization. Issue #13 adds scoped targeted GET and conditional PUT for the [executor](execution.md). PUT is refused unless `allow_write=True`; creates require `If-None-Match: *`, updates require a strong `If-Match`. Transport alone does not authorize ownership: use the executor. No POST/DELETE, discovery by display name, automatic worker or invitation sending is provided. The default CLI stays offline; all read acceptance below remains read-only.

## Install and run unit tests

From the repository root on Linux/macOS with Python 3.12+:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -p 'test_caldav.py' -v
python -m unittest discover -s tests -p 'test_events.py' -v
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
python -m pip check
```

Expected: 29 CalDAV tests (DAV-001–029), 13 normalization tests (NORM-001–013), 141 documented/discovered tests and 141 passing tests. No credentials, Nextcloud or Google requests are needed. [The catalogue](test-cases.md) lists each input/expected result. HTTP/XML/iCalendar fixtures are local test constants; Google conversion fixtures are local dictionaries.

## Set up a dedicated Nextcloud test calendar

1. Log into Nextcloud at the intended server with a dedicated test account, or use an explicitly authorized disposable calendar. Ensure the Calendar app is enabled. In Calendar, select **+ New calendar**, name it `Calendar Sync UAT`, and select an explicit timezone such as Europe/Madrid. Do not import or modify the production master calendar.
2. Obtain this calendar's exact CalDAV collection URL from your server's calendar/client configuration or administrator. It normally has the shape `https://nextcloud.tcadc.com/remote.php/dav/calendars/USER_ID/CALENDAR_URI/`. Use the actual internal user ID and collection URI, not display names; a renamed calendar may keep its old URI. Preserve the trailing slash. Do not use a public share, export/iCal subscription, browser app URL, principal URL or account-wide DAV base. This adapter intentionally does not discover the URL for you.
3. Confirm the chosen login account can view the calendar's event details. Read-only sharing is sufficient for these adapter tests. Browser SSO/Keycloak sessions are not CalDAV credentials; obtain a separate app/device password using the account's personal **Security → Devices & sessions** settings, or ask the administrator if that option is unavailable. Name it `Calendar Sync UAT` and use it only for this test. No OAuth client or Google verification is involved in Nextcloud authentication.
4. Store that app password in an ignored private file using the block below. It prompts through the terminal without putting the secret in shell history. Run it once; it refuses to overwrite an existing file. For intentional rotation stop test clients and use a new filename/password rather than deleting a live credential.

```sh
mkdir -p secrets
chmod 700 secrets
python - <<'PY'
import getpass
import os
from pathlib import Path

path = Path('secrets/nextcloud-uat-password')
password = getpass.getpass('Nextcloud UAT app password (not echoed): ')
if not password:
    raise SystemExit('No password entered; file not created')
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, 'w') as stream:
    stream.write(password)
print('Saved private app-password file; contents not displayed')
PY
export NEXTCLOUD_UAT_USERNAME='replace-with-the-login-username'
export NEXTCLOUD_UAT_CALENDAR_URL='replace-with-the-exact-https-caldav-calendar-url-ending-in-/'
export NEXTCLOUD_UAT_PASSWORD_FILE="$PWD/secrets/nextcloud-uat-password"
```

Replace both placeholders. The variables above are script inputs, not automatically loaded CLI settings; `.env` does not provision this adapter. The file must be regular, owned by the process user and have no group/other access (normally 0600); symlinks, empty or multiline passwords are rejected. One trailing line ending is tolerated for files prepared by an editor. Keep the containing directory private (0700). The app password may authorize more than one calendar; the adapter's explicit URL scope is a client boundary, not a server-side per-calendar grant. Use a limited account/calendar share appropriate to your intended access.

Sources: [Nextcloud calendar management](https://docs.nextcloud.com/server/stable/user_manual/en/groupware/calendar.html) and [device-specific passwords](https://docs.nextcloud.com/server/stable/user_manual/en/session_management.html). UI wording depends on the installed server/Calendar version; if you cannot identify the collection URL, obtain it from the administrator rather than trying public links or disabling security.

## Read semantics and limits

The only HTTP exception is an explicit `allow_http_loopback=True` together with a matching HTTP `origin_url` for local testing. See NC-UAT-06 below. The accepted HTTP hosts are exactly `localhost`, standard loopback IPv4 literals, or `::1`; public/private LAN hosts, shorthand numeric addresses and lookalike names are rejected. Prefer literal 127.0.0.1/::1 to avoid hostname resolution. No redirects, foreign ports/origins or TLS-verification bypass are enabled by this option. HTTPS remains the default for every normal deployment.

`CalDAVCalendar(calendar_url, username, password_file)` defaults to the trusted origin `https://nextcloud.tcadc.com`. A different deployment requires an explicit `origin_url`, HTTPS unless using the constrained local-test exception above; it is not inferred from server responses. Both the configured calendar and every reported resource must stay on that exact scheme/host/port and within the collection. Resource hrefs must be direct children; relative/same-origin absolute percent-encoded hrefs are canonicalized. Credentials in URLs, queries/fragments, traversal, double encoding, encoded separators and foreign/nested resource references are rejected. Hrefs are validated, never fetched.

One authenticated `REPORT calendar-query` selects VEVENT resources at Depth 1 and requests calendar-data/ETag. TLS verification, 5/30-second connect/read timeouts and refused redirects are fixed; environment proxy/netrc settings are disabled. No browser flow or fallback discovery is attempted. Transport failures do not automatically retry in this version: rerun the complete read after resolving transient errors. The future worker can schedule bounded retries.

The streamed response is bounded to 8 MiB and 10,000 resources by default (constructor overrides available). XML DTDs/entities are forbidden. Every member must have successful properties, calendar-data, a strong quoted ETag and a consistent UID. Duplicate hrefs/UIDs, bad XML/iCalendar, non-success member properties, explicit limit errors or exceeded bounds reject the entire read. A valid empty report returns `[]`; it never authorizes deletion. A server that silently omits resources cannot be detected by this client; the report is not a transactional calendar snapshot. There is no silent pagination/truncation fallback.

`CalendarResource` carries href, ETag, UID, raw calendar-data bytes and `skip_reason`. Its repr omits private values; do not log `.data` or IDs in review evidence. Resources with recurrence/instances, cancellation, unsupported components such as alarms, or unsupported time representation are preserved with skip codes; they are not flattened or written. Malformed identity/required response properties reject the whole report. An event using DURATION/implicit end instead of explicit DTSTART/DTEND, floating times, mixed boundary types or nonpositive duration is marked `unsupported_event_time`.

Provider diagnostics suppression is shared with Google in `security.py` and still uses process-global logging/wire settings. Use it only in the single-threaded worker/standalone checks, not concurrent web threads. No custom CA/proxy, Windows support, calendar provisioning, recurring-event interoperability or write execution is added here.

## Event normalization and serialization

`normalize_google_event(dictionary)` returns `NormalizationResult(event, reason, omitted_fields)`. It accepts ordinary single events with source ID, optional string summary/description/location, and valid start/end boundaries. Omitted text fields become empty strings. It never mutates the input or accesses a provider.

| Input | Result / fixed reason |
| --- | --- |
| Offset-aware timed boundaries | Convert both instants to UTC; preserve elapsed time across DST. If an IANA zone is supplied, its actual offset must agree. |
| Local datetime plus IANA timeZone | Accept only an unambiguous real local instant; reject DST gaps/folds as `ambiguous_or_nonexistent_time`. |
| All-day date boundaries | Preserve dates and exclusive DTEND; do not turn calendar days into UTC hours. |
| Cancelled event | `cancelled`, no normalized event. |
| recurrence, recurringEventId or originalStartTime marker | `unsupported_recurrence`, no expansion/flattening. |
| Non-default Google eventType | `unsupported_event_type`. |
| Missing timezone, offset mismatch, invalid boundaries/duration | `floating_time`, `timezone_offset_mismatch`, `invalid_time` or `invalid_duration`. |
| Nonzero fractional seconds | `unsupported_subsecond`; subsecond data is not silently truncated. |
| Attendees/organizer/creator, conference/link, attachments, reminders, visibility/transparency/color | Ordinary event can be `supported_with_omissions`; omitted property names are returned for a future audit record. These fields are not serialized. |

Normalized values provide deterministic SHA-256 fingerprints over supported fields and identity. Key order and omitted-field changes do not alter that fingerprint; a supported-field change does. Timed fields and text newlines are canonicalized. Privacy/visibility, participation, reminders and conferencing are not reproduced; choose destination calendar access appropriately before any writes. The normalization library returns omission/skip metadata without persisting it; the live executor records action/reason codes, not a detailed per-field omission history.

`serialize_event(normalized, uid, stamp=aware_datetime)` returns RFC 5545 bytes for one VCALENDAR/VEVENT. The caller supplies a stable UID and explicit stamp; UID generation/ownership marking is deferred to #12. Timed fields serialize in UTC, date fields remain DATE with exclusive DTEND, TEXT values are escaped and UTF-8 lines folded by `icalendar`. There is no METHOD, attendee, organizer or alarm component. The function has no upload side effects. Never treat caller-supplied UID alone as proof that an existing resource is owned.

Protocol/library sources: [CalDAV RFC 4791](https://www.rfc-editor.org/rfc/rfc4791), [iCalendar RFC 5545](https://www.rfc-editor.org/rfc/rfc5545) and [icalendar API](https://icalendar.readthedocs.io/en/stable/reference/api/icalendar.cal.calendar.html).

## Acceptance cases

Use [the execution record](test-evidence.md). Record each subcase independently; fake tests are not live server acceptance. No real Nextcloud/Google access was performed by the author. Live NC-UAT-03–06 remain Not run until the reviewer executes them.

| ID | Scenario / prerequisites | Expected result | Automated mapping |
| --- | --- | --- | --- |
| NC-UAT-01 | Offline conversion block below; installed dependencies, no credentials | Timed/all-day serialization succeeds; recurrence skipped; no network/files | NORM-001–013 |
| NC-UAT-02 | Offline missing-secret block below; no credentials | Fixed missing-secret code before network | DAV-005 |
| NC-UAT-03 | Live disposable calendar with exactly three fixtures and app password | Three resources, two supported and one recurring skip; stable repeated read; unchanged UI | DAV-001, DAV-012; actual server compatibility manual |
| NC-UAT-04 | Same test secret temporarily set to 0644, then restored to 0600 | Permission failure before request; success after restoration | DAV-005 |
| NC-UAT-05 | Revoke only dedicated UAT app password after other cases | Fixed authentication failure, no raw secrets or browser redirect-following | DAV-006; live revocation manual |
| NC-UAT-06 | Optional HTTP test server on localhost:8090, explicit matching origin and opt-in | Same stable read as NC-UAT-03; default HTTP and non-loopback HTTP still rejected | DAV-016–020; live localhost compatibility manual |

### NC-UAT-01: offline conversion smoke

Copy the complete shell block including its opening/closing delimiters:

```sh
python - <<'PY'
from datetime import date, datetime, timezone
from icalendar import Calendar
from calendar_sync.events import normalize_google_event, serialize_event

timed = {'id': 'uat-timed', 'summary': 'UAT timed',
         'start': {'dateTime': '2026-03-29T01:30:00+01:00', 'timeZone': 'Europe/Madrid'},
         'end': {'dateTime': '2026-03-29T03:30:00+02:00', 'timeZone': 'Europe/Madrid'}}
all_day = {'id': 'uat-day', 'start': {'date': '2026-03-29'}, 'end': {'date': '2026-03-30'}}
stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
event = normalize_google_event(timed).event
assert (event.end - event.start).total_seconds() == 3600
component = Calendar.from_ical(serialize_event(event, 'uat-timed', stamp=stamp)).walk('VEVENT')[0]
assert component.decoded('DTSTART') == event.start
day = normalize_google_event(all_day).event
component = Calendar.from_ical(serialize_event(day, 'uat-day', stamp=stamp)).walk('VEVENT')[0]
assert component.decoded('DTSTART') == date(2026, 3, 29)
assert component.decoded('DTEND') == date(2026, 3, 30)
assert normalize_google_event({**timed, 'recurrence': ['RRULE:FREQ=DAILY']}).reason == 'unsupported_recurrence'
print('PASS: UTC/DST and all-day roundtrip; recurrence skipped; no writes performed')
PY
```

Expected exit 0 and the PASS line. Cleanup: none.

### NC-UAT-02: missing app-password file

```sh
python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from calendar_sync.caldav import CalDAVCalendar, CalDAVError

with TemporaryDirectory() as directory:
    calendar = CalDAVCalendar(
        'https://nextcloud.tcadc.com/remote.php/dav/calendars/test/uat/',
        'unused-test-login', Path(directory) / 'missing-password',
    )
    try:
        calendar.list_resources()
    except CalDAVError as error:
        assert str(error) == 'caldav_secret_missing', 'FAIL: unexpected reason'
        print('PASS: missing app password rejected before network access')
    else:
        raise SystemExit('FAIL: missing app password accepted')
PY
```

Expected exit 0 and PASS; temporary directory is removed automatically.

### NC-UAT-03: live read of explicit calendar

After setup, use the Nextcloud event editor to create each fixture exactly once on `Calendar Sync UAT`, on a selected future date in its explicit timezone:

| Title | All day | Time / repeat | Other controls |
| --- | --- | --- | --- |
| NC UAT timed | OFF | 09:00–10:00; does not repeat | No reminders, attendees or Talk conversation. |
| NC UAT all-day | ON | One date; does not repeat | No reminders or attendees; title alone does not set All day. |
| NC UAT recurring | OFF | 11:00–12:00; Daily, optionally three occurrences | No reminders or attendees; save the series. |

Open the editor's advanced/More view to check Reminders and Repeat controls. Ensure exactly these three event resources exist; recurring occurrences are one CalDAV resource. Do not add fixtures on every retry. Existing alarm/extra/recurrence-exception resources can alter skip counts; use the dedicated fixture and inspect controls rather than weakening assertions.

```sh
python - <<'PY'
from collections import Counter
import os
from calendar_sync.caldav import CalDAVCalendar, CalDAVError

http_mode = os.environ.get('NEXTCLOUD_UAT_ALLOW_HTTP_LOOPBACK', 'false')
if http_mode not in {'true', 'false'}:
    raise SystemExit('NEXTCLOUD_UAT_ALLOW_HTTP_LOOPBACK must be true or false')
try:
    calendar = CalDAVCalendar(
        os.environ['NEXTCLOUD_UAT_CALENDAR_URL'], os.environ['NEXTCLOUD_UAT_USERNAME'],
        os.environ['NEXTCLOUD_UAT_PASSWORD_FILE'],
        origin_url=os.environ.get('NEXTCLOUD_UAT_ORIGIN_URL', 'https://nextcloud.tcadc.com'),
        allow_http_loopback=http_mode == 'true',
    )
    first = calendar.list_resources()
    second = calendar.list_resources()
except CalDAVError as error:
    raise SystemExit(str(error)) from None
counts = Counter(resource.skip_reason or 'supported' for resource in first)
print('Resource count:', len(first))
print('Supported count:', counts['supported'])
print('Recurring skip count:', counts['unsupported_recurrence'])
assert len(first) == 3 and counts == {'supported': 2, 'unsupported_recurrence': 1}, 'FAIL: unexpected fixture/skip counts'
assert {(item.href, item.etag) for item in first} == {(item.href, item.etag) for item in second}, 'FAIL: calendar changed between reads'
print('PASS: stable explicit-calendar reads; recurrence skipped; no writes performed')
PY
```

Expected exit 0 with count 3, supported 2, recurrence 1 and PASS. Reopen the UI to confirm events remain unchanged. These count/ETag assertions do not independently prove every fixture's title/type or server-side access policy; verify the selected calendar and controls in the UI too. No raw event bodies, URLs, UIDs, ETags or passwords should be pasted into the PR. If a fixture assertion fails, report only counts/reason codes.

### NC-UAT-04: reject broad secret permissions and recover

Stop other test clients, run `chmod 644 "$NEXTCLOUD_UAT_PASSWORD_FILE"`, then rerun the NC-UAT-03 block. Expect nonzero exit with `caldav_secret_permissions` before any request. Restore immediately with `chmod 600 "$NEXTCLOUD_UAT_PASSWORD_FILE"` even if another step fails; rerun NC-UAT-03 and expect its original PASS. This modifies only the disposable local secret's permissions, not calendar contents.

### NC-UAT-05: revoke only the test grant

After all other live cases, revoke the dedicated `Calendar Sync UAT` device/app password in Nextcloud's Security settings. Rerun NC-UAT-03: expect nonzero exit with `caldav_authentication_failed` (401). If the deployment redirects to browser SSO instead, it is rejected as `caldav_redirect_rejected`; record that compatibility issue rather than claiming successful CalDAV authentication. Never revoke a production grant for this case. A new test password is needed for further live tests.

### NC-UAT-06: optional local HTTP testing

Use this only with a disposable Nextcloud/test password running on your local machine. HTTP sends the app password without TLS; this opt-in is not for a LAN IP, remote hostname or production server. A local container's port must be published to your PC, for example localhost:8090. Ensure Nextcloud trusts the chosen local host and emits relative or same-origin CalDAV hrefs. `localhost` and `127.0.0.1` are different origins; do not mix them between the configured URL/origin and server-generated absolute hrefs. Nextcloud SSO redirects remain rejected.

Keep the private test secret/login from setup. Replace USER_ID/CALENDAR_URI with the actual collection path, then run NC-UAT-03's complete updated script:

```sh
export NEXTCLOUD_UAT_ORIGIN_URL='http://localhost:8090'
export NEXTCLOUD_UAT_CALENDAR_URL='http://localhost:8090/remote.php/dav/calendars/USER_ID/CALENDAR_URI/'
export NEXTCLOUD_UAT_ALLOW_HTTP_LOOPBACK=true
```

The script passes `origin_url='http://localhost:8090'` and `allow_http_loopback=True` explicitly. Setting these UAT variables does not change the main CLI's production configuration. Expected result is NC-UAT-03's count 3 / supported 2 / recurrence skip 1 / stable-read PASS. Without the opt-in, expect `caldav_https_required`; with a different origin/port expect `caldav_calendar_outside_origin`; non-loopback HTTP remains `caldav_http_loopback_only`. No credential checks, XML limits or read-only safeguards are relaxed.

After local testing, run `unset NEXTCLOUD_UAT_ORIGIN_URL NEXTCLOUD_UAT_ALLOW_HTTP_LOOPBACK` and restore `NEXTCLOUD_UAT_CALENDAR_URL` to the intended HTTPS test URL before another environment's tests. Do not change `NEXTCLOUD_URL` to HTTP: its default CLI settings still require HTTPS. For a remote server use HTTPS, or an appropriately configured local tunnel with matching loopback hrefs; the adapter does not grant a general insecure-HTTP override.

### NC-UAT-07: diagnose a failed read without exposing secrets

Prerequisites: current review branch, the same private secret/explicit test URL as NC-UAT-03, and optionally local HTTP settings from NC-UAT-06. Rerun NC-UAT-03 or PLAN-UAT-04 unchanged after updating code. Expect a fixed code from the error table if the read fails, never raw exception text or response content. `caldav_invalid_calendar_data` means a calendar object could not be safely parsed/validated; preserve the calendar and report only the code/server context. Transport and XML problems have separate codes. The legacy generic code remains for unclassified failures.

For local availability only, `curl --silent --show-error --output /dev/null --write-out 'HTTP %{http_code}\n' --max-time 5 http://localhost:8090/status.php` makes an unauthenticated status request. HTTP 200 demonstrates reachability, not valid credentials, the correct collection or valid calendar resources. Do not add passwords or raw header/body output. Apply the matching remedy and rerun; record the new code and eventual PASS in test-evidence.md. Do not delete events, relax parsers or bypass complete-read requirements to hide failures. This diagnostic case is Not run against authenticated calendars by the author; its fixed-code unit cases are DAV-021–023.

### Cleanup and safe errors

Stop test clients, restore permissions, revoke only the dedicated test app password, remove only the disposable calendar/fixtures if no longer needed, and securely remove only its local test password file. Unset `NEXTCLOUD_UAT_USERNAME`, `NEXTCLOUD_UAT_CALENDAR_URL`, `NEXTCLOUD_UAT_PASSWORD_FILE`. Never remove production calendars, credentials or SQLite mappings during test cleanup.

| Error | Next action |
| --- | --- |
| caldav_connection_failed | No usable connection to the origin. Check local container/listening port, hostname resolution and network access. |
| caldav_timeout | Connection/read timed out. Check server health/report load and network; no partial plan is accepted. |
| caldav_response_interrupted | Stream disconnected or chunking/compression was invalid. Check server/proxy health before retrying. |
| caldav_xml_invalid / xml_rejected | Malformed XML or forbidden DTD/entities. Check DAV/proxy configuration; do not disable safe parsing or share raw responses. |
| caldav_https_required / http_loopback_only / calendar_outside_origin | For local HTTP only, supply explicit matching loopback origin and opt-in; otherwise use HTTPS. Do not relax remote/production transport. |
| caldav_secret_missing / secret_permissions / secret_format | Correct the exact private app-password file/path/permissions; do not paste its contents. |
| caldav_authentication_failed | Verify login username and test app password; browser SSO credentials alone are insufficient. |
| caldav_permission_denied / calendar_not_found | Check calendar event-detail sharing and actual CalDAV collection URL, including trailing slash/internal IDs. |
| caldav_redirect_rejected / tls_failed | Resolve the server/proxy HTTPS/CalDAV configuration; no redirect-following or TLS bypass. |
| caldav_resource_outside_calendar / unsafe_path | Check exact collection URL and server hrefs through an administrator; do not forward credentials to another origin. |
| caldav_partial_response / response_limit / resource_limit | Entire read failed; reduce test dataset or deliberately raise limits where appropriate. No deletion or partial sync. |
| caldav_invalid_or_incomplete_read / invalid_response / invalid_calendar_data / invalid_etag / duplicate_resource | Report fixed code with server/version context; preserve the failing calendar and do not publish raw data. |

Keep setup/operator, tested commit, environment, NC-UAT case/subcase status and sanitized evidence in [the execution record](test-evidence.md). Conditional writes require separate [execution acceptance](execution.md); destination ownership adoption is refused. Continuous sync, Docker secret mounts and production rollouts remain future work.
