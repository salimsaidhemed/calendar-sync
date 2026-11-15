# Continuous worker and health

Issue #15 wires the executor into `calendar-sync worker` and Compose. Google is authoritative; only supported single events are copied to the selected Nextcloud calendar. Dry-run is the default, recurring events are skipped, destination edits conflict and nothing is deleted. No UI/Keycloak or v0.1.0 release is included. The bare `calendar-sync` command and bare Docker image remain offline checks; Compose explicitly selects the worker.

## Configuration and commands

From the repository root, install the project as described in [testing.md](testing.md). Local Python does **not** load `.env`; export variables in the same shell. Compose reads `.env`. UAT variables from previous guides are not worker settings; see the exact conversion below.

| Variable | Default / requirement |
| --- | --- |
| `SYNC_DRY_RUN` | `true`; only literal `false` enables remote writes. Booleans are case-sensitive `true`/`false`. |
| `SYNC_CONFIGURATION_ID` | `master`; stable identifier, never change to bypass a conflict. |
| `GOOGLE_CALENDAR_ID` | Required exact calendar ID; `primary` alias refused. |
| `GOOGLE_TOKEN_FILE` | Required absolute private token-file path. |
| `NEXTCLOUD_URL` | `https://nextcloud.tcadc.com`; explicit trusted origin. |
| `NEXTCLOUD_CALENDAR_URL` | Required exact CalDAV calendar URL with trailing slash, within trusted origin. |
| `NEXTCLOUD_USERNAME`, `NEXTCLOUD_PASSWORD_FILE` | Required login and absolute private app-password-file path; no password values in environment. |
| `NEXTCLOUD_ALLOW_HTTP_LOOPBACK` | `false`; `true` only permits an exactly matching explicit loopback HTTP origin/calendar. |
| `SYNC_STATE_FILE`, `SYNC_HEALTH_FILE` | `/data/calendar-sync.sqlite3`, `/data/health.json`; absolute distinct paths, also distinct from credentials and lock/SQLite sidecar paths. |
| `SYNC_POLL_SECONDS` | 300; integer 1–86400. |
| `SYNC_MAX_BACKOFF_SECONDS` | 3600; integer at least poll interval, at most 86400. |
| `SYNC_HEALTH_MAX_AGE_SECONDS` | 7200; greater than poll interval, at most 172800. Allow enough time for a complete read/write cycle. |

Single cycle: `calendar-sync worker --once` exits 0 for a healthy cycle, 1 for a failed/conflicted cycle, 2 for invalid configuration/startup/state failures. Continuous mode retries failed cycles until stopped; failed cycles do not exit the process. Startup failures exit 2 so Compose may restart the service. All diagnostics contain fixed codes and aggregate counts, never provider exceptions, paths, IDs, titles or tokens.

```sh
calendar-sync worker --once
calendar-sync health
calendar-sync worker
```

`health` opens only the small JSON health file; it does not contend for the SQLite lock, refresh credentials or contact providers. Exit 0 means a recent completed healthy cycle; 1 means missing/malformed/stale, starting, unhealthy or stopped. Dry-run health proves successful conflict-free reads/planning, **not applied synchronization**; the file/log explicitly records mode. Unsupported/missing events are skipped and alone do not make a cycle unhealthy. A successful `--once` leaves its last-cycle result until it expires; a continuous worker marks stopped on graceful shutdown. Abrupt termination leaves the prior snapshot until its age expires, so this is last-cycle health rather than a standalone process-liveness probe. Docker also tracks container lifecycle. Docker marks unhealthy containers but does not automatically restart them solely for health failures.

## Polling, signals and recovery

One worker owns one database lock throughout its lifetime. Configuration is created once; calendar IDs/URLs and credential-file references must exactly match on restart. Moving from local paths to container paths requires a deliberate configuration migration; do not discard mappings or change IDs to evade this check. Credential contents may rotate at the same trusted path. A competing worker fails before altering the active health file.

Healthy cycles wait the poll interval plus up to 10% jitter. Consecutive failed cycles use poll × 1, 2, 4, ... plus jitter, capped by max backoff. Success resets this streak. Google retains bounded per-request retries for transient failures and rate limits; its retry waits are interruptible. Google long Retry-After and CalDAV 429/503 hints defer the next cycle. Valid HTTP seconds/date hints are capped at 24 hours and may exceed the ordinary backoff ceiling; malformed CalDAV 429 hints fall back to 60 seconds. Hints longer than 24 hours require operator review before relying on unattended retries. A limited PUT retains its intent and defers readback/remaining events until a later cycle. No blind retry of uncertain PUTs is added; the durable three-attempt intent budget still applies. See [execution recovery](execution.md).

SIGTERM/SIGINT request a graceful stop and wake poll/retry waits. Reads check between Google pages/retries and CalDAV REPORT chunks, and the preview checks before starting each provider. The executor checks before starting work/events and immediately before an upload attempt. An in-flight PUT is allowed to finish readback/local confirmation; no subsequent event starts. An interrupted unapplied intent remains recoverable. Network calls have existing 5/30-second connect/read timeouts, not a whole-cycle deadline; an in-flight network request or stream chunk can delay stopping. Compose allows 120 seconds before Docker may kill the process. Forced termination relies on the durable journal on restart. No production hard-stop deadline is claimed.

## Docker Compose setup

The image runs as UID/GID 10001. Compose uses a read-only root filesystem, temporary `/tmp`, persistent named `/data` volume, no extra capabilities, no-new-privileges, an init process, health checking and `restart: unless-stopped`. It mounts only `secrets/google` writable (atomic refresh/lock creation), and `secrets/nextcloud` read-only. Never mount the whole credential workspace or bake secrets into the image.

1. Follow the selected personal/Workspace testing or production authorization path in [Google setup](google-setup.md) and exact calendar/app-password selection in [Nextcloud setup](nextcloud.md). Desktop OAuth runs on your workstation before starting Docker; the worker never launches a browser.
2. Create dedicated `secrets/google` and `secrets/nextcloud` directories outside versioned content. Securely place the authorized token at `secrets/google/token.json` and the app password at `secrets/nextcloud/password`. Do not print contents or paste secrets into commands/issues.
3. On a native Linux host, set only those dedicated directories/files to UID/GID 10001, directories 0700 and files 0600. For an already prepared **dedicated** directory, owner/admin commands are:

```sh
sudo chown 10001:10001 secrets/google secrets/nextcloud secrets/google/token.json secrets/nextcloud/password
sudo chmod 700 secrets/google secrets/nextcloud
sudo chmod 600 secrets/google/token.json secrets/nextcloud/password
```

Docker Desktop/Colima bind ownership translation varies. Verify effective ownership from the actual container; the libraries refuse token/password files not owned by the effective UID or with group/world permission. Do not relax these checks. Use dedicated correctly owned container storage if bind mounts cannot preserve permissions. The Google token directory must remain writable for refresh. Missing secret directories/files are a setup failure, not an invitation to bypass authorization.

4. Copy `.env.example` to `.env` and replace calendar/login placeholders; keep `SYNC_DRY_RUN=true`. The file contains settings and credential references only. Its `/run/secrets/...` paths refer to containers. Local Python needs your own absolute host paths.

```sh
cp .env.example .env
# Edit .env with the exact calendar identifiers and login, retaining dry-run.
docker compose config --quiet
docker compose build
docker compose run --rm worker calendar-sync worker --once
docker compose up -d worker
docker compose logs --tail 20 worker
docker compose ps
docker compose exec worker calendar-sync health
```

Expected: preview counts, dry_run true, no remote writes; health becomes healthy only after a complete conflict-free cycle. Review counts against known fixtures before setting `SYNC_DRY_RUN=false`. If accepting live writes, edit that setting explicitly, then recreate with `docker compose up -d --force-recreate worker`; changing `.env` alone does not change an existing container. Use only one worker per state volume. Store full production operational/retention/recovery acceptance under issues #16/#14; this feature does not declare the release production-ready.

To stop without losing state: `docker compose stop worker`. To remove containers while preserving state: `docker compose down` (without `--volumes`). Never remove the state volume to retry an uncertain write. Back up/restore with all clients stopped or SQLite backup API, as described in [state.md](state.md#backup-and-recovery). Do not erase an active lock or pending intents.

Container `localhost` is the container itself. The `http://localhost:8090` UAT option works with local Python, not a host Nextcloud service from a bridged container. For container testing use a trusted HTTPS destination or a reviewed networking setup; insecure LAN HTTP is refused. On Linux host networking is a separate deliberate deployment choice, not enabled by this Compose file.

## Unit tests and offline container acceptance

Python 3.12+, Linux/macOS, installed dependencies; no credentials/network for tests. From repository root:

```sh
python -m unittest discover -s tests -p 'test_worker.py' -v
python -m unittest discover -s tests -p 'test_executor.py' -v
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
python scripts/smoke_worker.py
docker build -t calendar-sync:worker-test .
docker run --rm --network none --read-only calendar-sync:worker-test
docker run --rm --network none --read-only --tmpfs /tmp:rw,mode=1777 --mount "type=bind,src=$PWD/scripts,dst=/checks,readonly" calendar-sync:worker-test python /checks/smoke_worker.py
```

Expected: 18 worker tests, 30 executor tests, 167 total tests and 167 exactly catalogued IDs pass. The fake integration script reports dry-run, one conditional create and unchanged after restart, with one mapping/resource total and working health. It uses no credential files or provider network, cleans temporary state automatically, and can run under container UID 10001. Docker build needs registry access; offline checks run with network disabled. CI repeats unit/catalogue, image build/offline integration and Compose configuration checks.

## User acceptance cases

Record each case separately using [test-evidence.md](test-evidence.md). Automated/fake tests do not establish live interoperability. At implementation time, all live worker cases are **Not run**; credential-free automated procedures are separate evidence.

| Case | Prerequisites, action and observable result | Coverage |
| --- | --- | --- |
| WORK-UAT-01 | Run offline commands above. All tests and fake/container integration pass; no credentials or provider traffic. Temporary test state cleaned automatically. | WORK-001–018; EXEC-026–030; OPS-03 |
| WORK-UAT-02 | Complete disposable setup below, then `calendar-sync worker --once`. Dry-run true, two create proposals (or two unchanged if already applied), no new remote events; exit 0. `calendar-sync health` returns 0. | WORK-001–004, WORK-012, WORK-015 |
| WORK-UAT-03 | Start `calendar-sync worker` in the foreground, observe two dry-run cycles, press Ctrl-C during poll wait. Exit 0, health returns 1/stopped; state can reopen. | WORK-008, WORK-016–017 |
| WORK-UAT-04 | While one worker is running, start the same command in another shell with identical variables. Second exits 2 with fixed startup/state code, first continues and its health file is untouched. Stop first before further tests. | WORK-007 |
| WORK-UAT-05 | Dedicated fixtures only: set SYNC_DRY_RUN=false, run --once twice. First applies two new events (or unchanged if already copied), second unchanged; two destination events/mappings total. Stop/restart continuous worker and verify no duplicates. | WORK-005; EXEC-002–003 |
| WORK-UAT-06 | Edit a copied destination event; rerun --once. Conflict=1, exit 1, health 1/unhealthy, edit preserved. Resolve only through operator review; no reset/deletion as a retry strategy. | WORK-011; EXEC-004–005 |
| WORK-UAT-07 | Use a dedicated fully prepared Compose test deployment. Start, inspect mode/health, stop/start, confirm stable state and no duplicates; verify settings above and secret ownership. No restart based solely on unhealthy status. | WORK-005–007, WORK-012–013; offline Compose validation; actual deployment manual |

### Reuse disposable UAT settings safely

Complete [EXEC-UAT-02 setup](execution.md#disposable-live-setup-exec-uat-02-through-06), including exactly two timed/all-day fixtures on isolated calendars. Begin before the deliberate conflict/deletion cases, or manually restore those fixtures first. Existing successfully applied EXEC fixtures are valid and should be unchanged, not recreated. Do not point these cases at production calendars. Personal/Workspace **testing** setup paths apply; production paths are Not applicable to these disposable tests.

After confirming the existing UAT variables contain real absolute paths, copy this whole block in the same activated project shell:

```sh
export SYNC_CONFIGURATION_ID='exec-uat'
export GOOGLE_CALENDAR_ID="$GOOGLE_UAT_CALENDAR_ID"
export GOOGLE_TOKEN_FILE="$GOOGLE_UAT_TOKEN_FILE"
export NEXTCLOUD_URL="$NEXTCLOUD_UAT_ORIGIN_URL"
export NEXTCLOUD_CALENDAR_URL="$NEXTCLOUD_UAT_CALENDAR_URL"
export NEXTCLOUD_USERNAME="$NEXTCLOUD_UAT_USERNAME"
export NEXTCLOUD_PASSWORD_FILE="$NEXTCLOUD_UAT_PASSWORD_FILE"
export NEXTCLOUD_ALLOW_HTTP_LOOPBACK="$NEXTCLOUD_UAT_ALLOW_HTTP_LOOPBACK"
export SYNC_STATE_FILE="$EXEC_UAT_STATE_FILE"
export SYNC_HEALTH_FILE="$PWD/.local/exec-uat/worker-health.json"
export SYNC_DRY_RUN='true'
export SYNC_POLL_SECONDS='10'
export SYNC_MAX_BACKOFF_SECONDS='60'
export SYNC_HEALTH_MAX_AGE_SECONDS='120'
calendar-sync worker --once
calendar-sync health
```

Shell paths containing `$PWD` must use double quotes when initially exported; single quotes preserve a literal `$PWD` and are rejected. Worker and execution scripts must share the same state/configuration/credential references for these reuse cases. Do not run both concurrently. For WORK-UAT-03/04 use `calendar-sync worker`; for 05 explicitly `export SYNC_DRY_RUN='false'` before the documented calls. Changes in one terminal do not update another shell's variables.

Failures: fixed `worker_startup_or_state_failed` means validate settings, absolute paths, existing configuration and ownership/lock. Runtime failures mark unhealthy and retry conservatively; Google/Nextcloud auth setup remains in their guides. Do not expose token contents to troubleshoot. Detailed provider codes are deliberately suppressed in the worker log; use the separate read-only adapter diagnostics with the same private files if necessary.

Cleanup: stop all test workers, set SYNC_DRY_RUN=true, preserve state while reviewing failures, then remove only dedicated disposable calendars/test state as described in [execution cleanup](execution.md#failure-handling-and-cleanup). `docker compose down` preserves named state; do not use volume deletion for recovery. Remove the dedicated test image through normal local cleanup if no longer needed. Live results, both signals, conflict handling and actual mount permissions must be recorded separately from automated evidence.
