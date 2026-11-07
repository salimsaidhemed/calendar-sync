# Google Workspace authorization and reads

Implemented for issue #9. This adds Google authorization and a read-only adapter; it does not enable syncing or any Nextcloud writes. Default `calendar-sync` behavior remains credential-free and offline. Provider operations are not yet integrated into the SQLite worker.

## One-time authorization on your workstation

1. In your Google Cloud project, enable the Calendar API and configure the OAuth consent screen for your organization. For Google Workspace, the administrator may need to approve the app and requested scope. Use an Internal audience if your organization/project is eligible; otherwise configure permitted test users and publishing status appropriately.
2. Create an OAuth client of type **Desktop app** and download its JSON configuration. Web clients, service accounts, domain-wide delegation and Gmail authorization are not supported by this adapter.
3. Place the client file under ignored `secrets/` and restrict access. Use separate client and token paths. Run the authorization command on the same workstation as your browser, outside the headless worker/container:

```sh
mkdir -p secrets
chmod 700 secrets
# Place the downloaded Desktop client JSON at secrets/google-client.json.
chmod 600 secrets/google-client.json
calendar-sync authorize-google \
  --client-file "$PWD/secrets/google-client.json" \
  --token-file "$PWD/secrets/google-token.json"
```

The command opens your system browser and listens only on `127.0.0.1` at an ephemeral port for up to 180 seconds. It requests offline access and explicit consent with PKCE and library-managed OAuth state validation. Authorization URLs/codes are not printed. Complete consent using the account that can read the master calendar; the command prints only success or a fixed error code. Failed authorization leaves any existing token file intact.

The requested scope is exactly `https://www.googleapis.com/auth/calendar.events.readonly`. It permits event reads across calendars available to the account; OAuth does not limit this grant to one selected calendar. The adapter restricts each read to the explicit ID you configure. Broader/different scopes and unexpected endpoints are rejected rather than silently reused. Authorize again if existing tokens use a different scope.

The adapter uses the Google-supported Desktop flow and the event list API. See [Google Desktop OAuth documentation](https://developers.google.com/identity/protocols/oauth2/native-app), [event list authorization and pagination](https://developers.google.com/workspace/calendar/api/v3/reference/events/list) and [OAuth token expiration/revocation](https://developers.google.com/identity/protocols/oauth2). Refresh tokens may expire or be revoked; External apps in Testing can have short-lived refresh tokens. Configure publishing/admin access according to your organization before relying on unattended refresh.

## Reading the explicitly selected calendar

In Google Calendar settings, open the intended master calendar and copy its Calendar ID from Integrate calendar. Prefer the exact stable ID rather than the `primary` alias. No display-name discovery is performed. Integration example for the upcoming worker:

```python
from calendar_sync.google_calendar import GoogleCalendar
from calendar_sync.google_oauth import GoogleError

calendar = GoogleCalendar('/run/secrets/google-token.json', 'master@example.invalid')
try:
    events = calendar.list_events()
except GoogleError as error:
    print(str(error))
else:
    print(f'Completed read: {len(events)} events')
```

`list_events` accumulates pages internally and returns only when pagination finishes. Page/transport errors, repeated page tokens, duplicate event IDs and malformed event lists reject the whole read. Empty intermediate pages with another token are followed. Defaults bound reads to 1,000 pages and 100,000 events; constructor overrides can increase those limits. Limit exhaustion is a failure, never a partial success. Google pagination is not a point-in-time snapshot: concurrent calendar edits may affect the result. No provider read can authorize a delete.

Reads use `singleEvents=false` and `showDeleted=true`: recurring masters/exceptions and cancellation markers remain available for the future planner to skip/audit. This adapter does not expand or transform recurring series. No incremental sync token or time-window filtering is used yet; each read fetches the full selected calendar history. Event bodies are returned in memory to the caller, not printed, logged or persisted by this adapter.

## Token storage and refresh

The client/token files must be regular, owned by the current process user and have no group/other permission bits (normally 0600). Symlink files are rejected. Token directories must be owned by that user with no group/other access (normally 0700). Provision a trusted directory; do not put secrets in shared writable directories. The token JSON includes access/refresh tokens and client configuration; never commit it or copy it into an image. Only credential file paths belong in SQLite.

Token reads/refresh and local authorization acquire a nonblocking `<token-file>.lock`. Competing access fails closed; retry after the other operation finishes. Expiring tokens refresh before a page request. Successful refresh writes a same-directory 0600 temporary file, flushes and atomically replaces the old token, then flushes the directory. Normal failures remove temporary files. A process crash can leave a `.oauth-*` file: treat it as a secret, and clean it only with all clients stopped. Do not delete a live lock file. An interrupted remote token exchange may require reauthorization; local atomicity cannot make Google's token issuance transactional.

For Docker deployment, mount a dedicated writable secret directory so token rotation can replace the file and create its lock. A read-only single-file token mount will fail when refreshing. Ensure ownership matches container UID 10001; restrict the directory to 0700 and files to 0600. Securely transfer the token to that directory after workstation authorization. Compose secret mounts and continuous-worker wiring are deferred to the operations milestone; the current Compose service still runs only the offline scaffold. No browser login is ever initiated automatically by token refresh or calendar reads.

## Failures, retries and diagnostics

TLS verification is enabled; redirects and proxy/netrc settings from the environment are disabled. Custom proxy/CA deployment configuration is not supported yet. Fixed Google HTTPS endpoints receive credentials; calendar IDs are encoded as one URL segment. Each page request has connect/read timeouts of 5/30 seconds. Transient connection/timeouts, 429, selected 5xx responses, and documented 403 rate-limit reasons retry at most four attempts by default (configurable up to eight), using exponential backoff and jitter. `Retry-After` seconds or dates up to 60 seconds are honored; longer delays fail with `google_retry_deferred` for a later worker run. Google-auth handles its bounded token-endpoint refresh retries separately. Request read timeouts are per operation, not a total wall-clock deadline for a whole calendar read.

Permission/authentication errors, redirects and TLS errors do not retry. A 401 fails the current read; there is no silent second browser login. Missing tokens return `google_authorization_required`; revoked credentials or refresh/storage failures return a sanitized `google_refresh_or_storage_failed`. Repair file permissions, check app/account access or rerun the explicit authorization command as appropriate. Raw upstream exceptions and response bodies are never included in adapter error messages.

OAuth and HTTP libraries can emit secrets under debug logging. The adapter temporarily disables process logging and HTTP wire diagnostics during sensitive calls and restores the previous settings afterward. It is designed for the current single-threaded worker/standalone authorization command: do not invoke it concurrently in threads of a future web server, because those diagnostic settings are process-global. Future UI integration must isolate provider work in the worker process. Avoid external request tracing/instrumentation that records headers or bodies.

Tests use fake tokens, fake browser flows and fake HTTP responses; no real credentials or Google requests are required. They cover refresh/revocation, atomic replacement, permissions, token contention, scope/endpoint rejection, complete pagination, retries, partial failure and diagnostic redaction.
