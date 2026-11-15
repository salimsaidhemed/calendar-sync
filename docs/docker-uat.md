# Reproducible local Docker UAT

This is the complete WORK-UAT-07 setup, including credential preparation and recovery. It uses [examples/uat/compose.yaml](../examples/uat/compose.yaml), not the production Compose file. No credentials are needed to review/validate the template; actual reads/writes require your own authorization. Keep original conflict-test calendars and state intact. This isolated fixture deployment intentionally uses its own configuration ID (`worker-docker-uat`), destination calendar and named volumes.

## Prerequisites and fixture controls — DOCKER-UAT-01

Use the intended reviewed worker commit, Docker/Compose with the daemon running, and Linux containers. The supplied route is for Nextcloud published at port 8090 on the **same Docker Engine** (including the Colima Linux VM). Host networking shares that engine's host, not an arbitrary macOS-only service. Docker Desktop host networking is separately supported as an opt-in on supported versions; see [Docker host networking](https://docs.docker.com/engine/network/drivers/host/). Do not silently replace localhost with a non-loopback HTTP hostname; the application refuses it. For production use trusted HTTPS, not this test-only host-network setup.

Stop existing native/test workers before preparing token copies. In Nextcloud create a **new empty** calendar `Worker Docker UAT`; copy its real CalDAV URL from its calendar menu, not the browser URL or display name. Create a **new Google calendar** `Worker Docker UAT` and record its exact ID. Choose the correct personal/Workspace testing authorization path from [google-setup.md](google-setup.md). The account must read this calendar, and Nextcloud's app-password account must write the destination.

On the new Google calendar create exactly these two **events**, explicitly selecting that calendar in the editor:

| Title | Editor controls |
| --- | --- |
| Docker UAT timed | 16 November 2026, 09:00–10:00, timezone UTC, All day OFF, Does not repeat, no guests/reminders |
| Docker UAT all-day | 16 November 2026, All day ON, one calendar day (exclusive API end 17 November), Does not repeat, no guests/reminders |

Using new empty calendars means the expected preview is **2 creates and 0 unsupported**. Zero total proposals is a successful empty read, not fixture acceptance: check source calendar ID, event calendar assignment and saved events. If deliberately reusing the earlier six-record Google fixture instead, record that variant explicitly: 2 creates and 4 unsupported (one recurring master, three cancelled). Do not mix expected counts between variants. Existing unrelated events in the destination are unowned and must remain untouched.

Place the already authorized Google token and Nextcloud app password in private local files as described in the provider guides. The input token is not the OAuth client JSON. Use separate client/token filenames. Never print file contents, grant calendar write scope to Google, or place credentials in URLs.

## Configure and prepare — DOCKER-UAT-02

From repository root:

```sh
cp -n examples/uat/.env.example examples/uat/.env
# Edit examples/uat/.env: exact source ID, new destination URL, login and real absolute file paths.
```

The file contains paths/settings only; replace every REPLACE value. Single-quoted `$PWD` is literal, not an absolute path: use actual absolute paths in this file. Bind sources must be visible to Docker's VM/file sharing (normally files under your home directory); a macOS temporary path outside the VM's shared mounts may report bind-source missing even when the host file exists. The initializer refuses to create missing host paths. Local Python does not load this file. Before defining a shell helper, clear inherited variables that would override the chosen Compose environment file:

```sh
unset SYNC_DRY_RUN GOOGLE_CALENDAR_ID NEXTCLOUD_CALENDAR_URL NEXTCLOUD_USERNAME
unset UAT_GOOGLE_TOKEN_SOURCE UAT_NEXTCLOUD_PASSWORD_SOURCE
uat() {
  docker compose --env-file examples/uat/.env \
    -p calendar-sync-uat07 -f examples/uat/compose.yaml "$@"
}
uat config --quiet
uat build worker
uat run --rm --no-deps prepare-credentials
```

Expected preparation PASS, with no secret contents printed. The setup-only container has no network and mounts the two host files read-only. It copies them once into dedicated named volumes, assigning directories0700/files0600 to UID/GID10001; your Mac file ownership remains unchanged. The worker runs unprivileged/read-only, mounts Google credentials writable for atomic refresh, and Nextcloud credentials read-only. No `sudo chown` on your Mac files is needed.

Initialization refuses an existing token/password, preventing accidental overwrite of a refreshed token. Run it once before workers start. If preparation partially fails or volumes already exist, inspect the cause; do not delete all volumes or reset state. Keep the same project name and configuration ID across restarts. Different names intentionally select independent state, not a retry mechanism.

## Read-only diagnostics and preview — DOCKER-UAT-03

Check public connectivity without credentials:

```sh
uat run --rm --no-deps worker python -c 'import requests; r=requests.get("http://localhost:8090/status.php",timeout=(3,5),allow_redirects=False); print("Nextcloud HTTP status:",r.status_code); raise SystemExit(0 if r.status_code==200 else 1)'
uat run --rm --no-deps worker calendar-sync worker --once
```

Expected HTTP200 and worker dry_run true/healthy with create2 and all other proposal counts0. The destination calendar stays empty. Worker exit0 is necessary but not sufficient: check fixture counts. Configuration/health may persist, but no applied mapping/run/intent/audit is recorded by dry-run.

For `worker_cycle_failed`, isolate providers without logging bodies, titles, IDs or secrets:

```sh
uat stop worker
uat run --rm --no-deps -T worker python - <<'PY'
import os
from calendar_sync.google_calendar import GoogleCalendar
from calendar_sync.google_oauth import GoogleError
from calendar_sync.caldav import CalDAVError
from calendar_sync.worker import WorkerSettings

settings = WorkerSettings.from_env(os.environ)
try:
    google = GoogleCalendar(settings.google_token_file, settings.google_calendar_id)
    print('Google records:', len(google.list_events()))
except GoogleError as error:
    print('Google read failed:', str(error))
try:
    print('Nextcloud resources:', len(settings.caldav().list_resources()))
except CalDAVError as error:
    print('Nextcloud read failed:', str(error))
PY
```

This performs read-only calendar access; Google may refresh its token. Fixed Google errors separate API/permission/auth problems; `google_refresh_or_storage_failed` can indicate rejected refresh or failed saving, not proof of expiry. External Testing calendar grants can expire after seven days. For reauthorization/replacement follow the next section rather than removing state.

## Renew only the Google token — DOCKER-UAT-04

With all clients stopped, authorize on your workstation using the original Desktop client and the **same host token path** configured in UAT_GOOGLE_TOKEN_SOURCE. Example paths below must be adjusted locally if yours differ:

```sh
calendar-sync authorize-google \
  --client-file "$PWD/secrets/google-uat-client.json" \
  --token-file "$PWD/secrets/google-uat-token.json"
```

Continue only after the explicit authorization-saved message. The container volume is a copy; changing a host file alone does not update it. Replace only that copied token atomically, retaining UID10001 and the Google directory's writable/private permissions:

```sh
uat run --rm --no-deps -T --user 0:0 --entrypoint python prepare-credentials - <<'PY'
import os
import shutil
import tempfile
from pathlib import Path

source = Path('/input/google-token.json')
directory = Path('/output/google')
target = directory / 'token.json'
assert source.is_file() and source.stat().st_size > 0, 'Source token missing'
assert not target.is_symlink(), 'Unexpected token symlink'
os.chown(directory, 10001, 10001)
os.chmod(directory, 0o700)
descriptor, temporary = tempfile.mkstemp(prefix='.token-', dir=directory)
try:
    with os.fdopen(descriptor, 'wb') as destination, source.open('rb') as origin:
        shutil.copyfileobj(origin, destination)
        destination.flush()
        os.fchown(destination.fileno(), 10001, 10001)
        os.fsync(destination.fileno())
    os.replace(temporary, target)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print('PASS: Google token replaced; database and Nextcloud password preserved')
PY
uat run --rm --no-deps worker calendar-sync worker --once
```

The explicit root helper only assigns ownership inside credential storage; no calendar request occurs during replacement. It does not change configuration IDs, mappings, attempts or Nextcloud credentials. Do not run initialization again or paste token contents into evidence. For production rotation use the controlled procedure in [operations.md](operations.md), keeping each deployment's references stable.

## Live cycle, repeat and restart — DOCKER-UAT-05

Only after the preview matches the two disposable fixtures, edit examples/uat/.env to SYNC_DRY_RUN=false. Do not rely on an exported variable or an old container picking up a changed file. Recreate explicitly:

```sh
uat up -d --force-recreate worker
uat logs -f --tail 20 worker
```

Expect first cycle applied2, next cycle unchanged2/applied0, no skipped/conflict/pending and healthy. Ctrl-C exits only the log viewer (often130); it does not stop the worker. In Nextcloud confirm exactly one timed copy and one all-day copy with the specified date/time, accounting for the UI display timezone.

```sh
uat restart worker
uat logs -f --since 30s worker
```

Wait for a **new** post-restart cycle: unchanged2/applied0, no duplicates. Historical log lines may also appear; record which cycle is after restart. Exit the log viewer, then:

```sh
uat exec worker calendar-sync health
echo "Health exit code: $?"
uat stop worker
uat ps -a
```

Expected health0 before stop and Exited(0) after graceful stop. Restore SYNC_DRY_RUN=true in examples/uat/.env. Editing a file does not alter a running container, so stop/recreate is part of acceptance. Keep all state/credential volumes.

## Results, cleanup and scope

Use [the evidence template](test-evidence.md). Report DOCKER-UAT-01–05 and linked WORK-UAT-05/07 separately: fixture preparation, public connectivity, provider diagnostics, credential renewal if needed, create/repeat/restart, health, graceful exit, visual date/type/duplicate check, dry-run restoration. The repository's template/automated checks do not establish live deployment acceptance. User-provided runtime evidence is summarized separately in [worker-uat-evidence.md](worker-uat-evidence.md), with unconfirmed visual/cleanup checks left open.

Use `uat down` without --volumes to remove containers while retaining state and refreshed credentials. Never use volume deletion or changing configuration ID as error recovery. After explicit evidence review, remove only the dedicated test calendars if no longer needed. Back up state before any destructive cleanup; token backups/volumes remain secrets. Production readiness and release approval remain separate under #14. For retention/backup/restore see [operations.md](operations.md).
