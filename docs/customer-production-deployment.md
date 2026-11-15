# Customer production deployment — Google Workspace

This guide is for a customer-controlled Linux server running one Google Workspace master calendar into one Nextcloud calendar, normally at https://nextcloud.tcadc.com. It requires no access by the developer to the customer server. Follow sections in order; all commands in a section must succeed before continuing. Replace every REPLACE value privately.

**Approval and scope:** deploy an owner-approved main commit, not an unreviewed feature branch. PR #30 supplies the worker. PR #31 adds this guide and the snapshot helper; review/merge it before using that helper on customer state. The full sequence below assumes an approved commit containing both. Release/safety sign-off remains #14; this document does not declare v0.1.0 released or establish production acceptance. A customer dry-run pilot can validate connectivity before live rollout, but it never substitutes for the live acceptance checklist.

## 1. Agree the supported behavior — CUSTOMER-UAT-01

The customer must understand the initial contract before enabling writes:

- Google is authoritative; ordinary single timed/all-day events flow to Nextcloud only.
- Recurring masters, instances and exceptions are skipped, not expanded. Cancelled/deleted source events do **not** delete their destination copies.
- Destination edits/missing mapped copies produce conflicts; no automatic winner, adoption/reset or conflict-resolution UI exists.
- Attendees/invitations, organizer identity, conferencing, reminders, attachments and privacy/availability fields are not reproduced. Choose destination access appropriately.
- No Google writes, bidirectional sync, Keycloak/UI, Chat or automatic account provisioning is included.

If the customer requires unsupported behavior for its workflow, do not enable live mode on the assumption that it is implemented. Record the exact source/destination calendar identities, authorized operators, selected release/commit, expected single/recurring event counts, backup owner and alert recipient in a protected deployment record. Do not put calendar IDs or credentials into public issues.

## 2. Prepare Google Workspace — CUSTOMER-UAT-02

Work with the customer's Workspace administrator:

1. Select a customer-controlled Workspace account that can read the intended source calendar's event details. Use the exact calendar ID from Google Calendar Settings → selected calendar → Integrate calendar. Free/busy-only permission is insufficient; the display name, OAuth client ID, iCal link and `primary` alias are not the exact source ID.
2. Create/select a **customer-owned organization-associated Google Cloud project**, separate from UAT, and enable Google Calendar API in it.
3. Configure Google Auth Platform branding/audience. For same-organization-only use, choose **Internal** when the project is eligible. A developer-owned personal/other-organization project is not automatically Internal to the customer. If Internal is unavailable or outside users are required, use the External production path and resolve applicable verification/exemption requirements with the administrator.
4. Create a **Desktop app** OAuth client and request exactly `https://www.googleapis.com/auth/calendar.events.readonly`. This implementation uses delegated user consent/loopback OAuth; service accounts/domain-wide delegation and Web-client callback deployments are not implemented.
5. In Workspace Admin Console → Security → Access and data control → API controls → Manage App Access, review the **exact client ID**, intended users/OU and read scope. Apply the needed access policy; do not blanket-trust unrelated clients. Admin controls, Calendar API enablement and calendar sharing are independent checks.
6. Download the Desktop client JSON securely to the authorization workstation. Do not put it in Git, an image, the customer .env or a public file share.

For policy details use [Google's audience comparison](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview), [Workspace app access controls](https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data), and the [full production setup path](google-setup.md#setup-wp-workspace-account-production-authorization). External/Testing calendar grants can expire after seven days; this is not the intended unattended-production setup. Internal/production grants remain revocable and can be affected by administrator/session policies; never promise permanent tokens.

### Authorize on a workstation with a browser

Use the reviewed checkout, Python3.12+ and an activated project virtual environment. Place the downloaded client at secrets/customer-google-client.json first. Use a customer account with the selected source access, not a developer's personal Gmail account.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
mkdir -p secrets
chmod 700 secrets
chmod 600 secrets/customer-google-client.json
calendar-sync authorize-google \
  --client-file "$PWD/secrets/customer-google-client.json" \
  --token-file "$PWD/secrets/customer-google-token.json"
```

Continue only after `Google authorization saved. No calendar events were modified.` The token directory/file must be owned by this workstation user with0700/0600 permissions. Never display token contents. The worker will refresh normally but will not launch an interactive browser; future reauthorization happens deliberately on a workstation.

## 3. Prepare Nextcloud — CUSTOMER-UAT-03

1. Confirm the Calendar app works over verified HTTPS at the intended Nextcloud origin.
2. Prefer a new empty customer destination calendar for first deployment, for example `Workspace Calendar Sync`. Copy its real CalDAV collection URL from calendar/client configuration; preserve the internal user ID, URI and trailing slash. The web application, public-share/export/feed or principal URL is not the collection URL.
3. Select a limited customer account/share with read/write access to that calendar and generate a dedicated device/app password in its personal Security settings. A Nextcloud/Keycloak browser session is not a CalDAV password. App passwords may grant wider account access than a single calendar; client URL scoping is not a server-side per-calendar permission restriction.
4. Store the generated password in a private workstation file without echoing it or putting it into shell history:

```sh
python - <<'PY'
import getpass
import os
from pathlib import Path

path = Path('secrets/customer-nextcloud-password')
password = getpass.getpass('Customer Nextcloud app password (not echoed): ')
if not password:
    raise SystemExit('Empty password refused')
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, 'w') as stream:
    stream.write(password)
print('Saved private password file; contents not displayed')
PY
```

This is a first-time creation block; it refuses an existing file. Record the device-token label for future controlled rotation. See [Nextcloud device passwords](https://docs.nextcloud.com/server/stable/user_manual/en/session_management.html) and [exact calendar selection](nextcloud.md#set-up-a-dedicated-nextcloud-test-calendar). Existing manually copied destination events are unowned; titles/dates are not used to merge them with managed copies. Inventory an existing destination instead of assuming no duplicates will be created.

## 4. Prepare the customer server — CUSTOMER-UAT-04

Assumptions: a supported 64-bit Linux host such as Ubuntu/Debian, a trusted operator account with approved sudo/Docker access, Git, system Python3 for local setup, Docker Engine and the Compose plugin. The application runs with Python3.12 inside the image; the server does not need a second host installation of the application's dependencies. Follow [Docker's official Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/) or the appropriate distribution instructions, rather than a convenience install script. Building on the actual host selects its architecture; no CPU/RAM/performance guarantee has been established for this workload.

Allow DNS and outbound verified HTTPS to Google OAuth/API endpoints, the package/image registries needed for builds, and the selected Nextcloud origin. Keep system time synchronized. No inbound Calendar Sync web port is required; no UI/public callback is deployed. Do not add host networking, localhost:8090, port publishing or HTTP exceptions from the local UAT setup to production.

On the server:

```sh
docker version
docker compose version
git --version
python3 --version
sudo install -d -o "$(id -u)" -g "$(id -g)" -m 0750 /opt/calendar-sync
git clone https://github.com/salimsaidhemed/calendar-sync.git /opt/calendar-sync
cd /opt/calendar-sync
```

If the directory already contains an installation, stop and use the controlled upgrade/recovery procedure rather than cloning over it or resetting its volumes. Docker access must be approved for this operator. Keep the deployment directory/configuration private; do not run commands that print all secrets/environment data.

### Where to get the SHA

A SHA is Git's commit identifier, not a separate download or Docker image ID. On the customer server, fetch main and print its full current commit:

```sh
cd /opt/calendar-sync
git fetch origin
export CUSTOMER_SYNC_COMMIT="$(git rev-parse origin/main)"
echo "$CUSTOMER_SYNC_COMMIT"
git show --no-patch --format=fuller "$CUSTOMER_SYNC_COMMIT"
```

The printed value is normally 40 hexadecimal characters. In GitHub, compare it with the commit shown for main and confirm the owner's reviewed PRs #30 and #31 are merged into that version. **Latest main is a candidate, not automatic deployment approval.** If your change process approved an older main commit, set CUSTOMER_SYNC_COMMIT to that full approved SHA instead. Do not use a feature-branch SHA, a PR number, an image ID or the literal REPLACE placeholder.

Verify the selected commit is on fetched main and contains the worker/recovery files **before** changing the checkout:

```sh
git cat-file -e "${CUSTOMER_SYNC_COMMIT}^{commit}"
git merge-base --is-ancestor "$CUSTOMER_SYNC_COMMIT" origin/main
git cat-file -e "${CUSTOMER_SYNC_COMMIT}:src/calendar_sync/worker.py"
git cat-file -e "${CUSTOMER_SYNC_COMMIT}:scripts/state_snapshot.py"
git checkout --detach "$CUSTOMER_SYNC_COMMIT"
git rev-parse HEAD
```

The final HEAD value must equal CUSTOMER_SYNC_COMMIT. These guards verify ancestry/file presence, not owner approval. If #31 is not merged, the recovery-file check can fail: wait for the reviewed main version rather than deploying its unmerged branch. Do not continue after any failed guard or discard local changes to force checkout. Do not invent a v0.1.0 tag before the release is approved. Record this exact SHA and the built image ID privately; current dependency/base-image ranges mean future builds of the same source may resolve different dependencies/base images.

## 5. Transfer and install credentials securely — CUSTOMER-UAT-05

On the **authorization workstation**, replace the SSH alias and stage through SSH/SFTP/SCP into a private customer operator directory:

```sh
export CUSTOMER_SSH='REPLACE_OPERATOR@REPLACE_SERVER'
ssh "$CUSTOMER_SSH" 'umask 077; mkdir -p "$HOME/.calendar-sync-staging"; chmod 700 "$HOME/.calendar-sync-staging"'
scp secrets/customer-google-token.json "$CUSTOMER_SSH:.calendar-sync-staging/google-token.json"
scp secrets/customer-nextcloud-password "$CUSTOMER_SSH:.calendar-sync-staging/nextcloud-password"
```

Use the customer's secure secret-transfer process if SSH copy is not permitted. Do not transfer the Desktop client into the runtime image. On the **server**, from /opt/calendar-sync and with all Calendar Sync workers stopped, install the files once into the base Compose bind directories. The helper refuses existing destinations and never prints contents; it does not change workstation file ownership:

```sh
cd /opt/calendar-sync
sudo env CUSTOMER_STAGE="$HOME/.calendar-sync-staging" python3 - <<'PY'
import os
import shutil
from pathlib import Path

stage = Path(os.environ['CUSTOMER_STAGE'])
files = (
    (stage / 'google-token.json', Path('secrets/google/token.json')),
    (stage / 'nextcloud-password', Path('secrets/nextcloud/password')),
)
for source, target in files:
    if source.is_symlink() or not source.is_file() or source.stat().st_size == 0:
        raise SystemExit('Staged credential missing/invalid')
    if os.path.lexists(target):
        raise SystemExit('Existing credential refused; use the rotation procedure')
Path('secrets').mkdir(mode=0o700, exist_ok=True)
for source, target in files:
    target.parent.mkdir(mode=0o700, exist_ok=True)
    os.chown(target.parent, 10001, 10001)
    os.chmod(target.parent, 0o700)
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as destination, source.open('rb') as origin:
        shutil.copyfileobj(origin, destination)
        destination.flush()
        os.fchown(destination.fileno(), 10001, 10001)
        os.fsync(destination.fileno())
print('PASS: runtime credentials installed privately for UID10001')
PY
```

The Google directory is writable by the worker for refresh/lock creation; Nextcloud's directory is mounted read-only. If installation partially fails, stop and inspect before retrying; do not erase state or overwrite a refreshed token. Once runtime validation and the customer's secret-backup policy are satisfied, securely remove only the no-longer-needed staged copies. Keep the customer-controlled OAuth client and recovery material in approved protected storage; do not delete the only recoverable grant/material as routine cleanup.

## 6. Configure the deployment — CUSTOMER-UAT-06

From /opt/calendar-sync, create .env from the example without overwriting an existing configuration:

```sh
umask 077
cp -n .env.example .env
chmod 600 .env
```

Edit .env to contain these production values, substituting the exact customer IDs/login/calendar URI. It contains **references/settings only**, never the token or password contents:

```dotenv
SYNC_DRY_RUN=true
SYNC_CONFIGURATION_ID=customer-master
SYNC_POLL_SECONDS=300
SYNC_MAX_BACKOFF_SECONDS=3600
SYNC_HEALTH_MAX_AGE_SECONDS=7200
SYNC_STATE_FILE=/data/calendar-sync.sqlite3
SYNC_HEALTH_FILE=/data/health.json
GOOGLE_CALENDAR_ID=REPLACE_EXACT_WORKSPACE_CALENDAR_ID
GOOGLE_TOKEN_FILE=/run/secrets/google/token.json
NEXTCLOUD_URL=https://nextcloud.tcadc.com
NEXTCLOUD_CALENDAR_URL=https://nextcloud.tcadc.com/remote.php/dav/calendars/REPLACE_USER/REPLACE_CALENDAR_URI/
NEXTCLOUD_USERNAME=REPLACE_LOGIN
NEXTCLOUD_PASSWORD_FILE=/run/secrets/nextcloud/password
NEXTCLOUD_ALLOW_HTTP_LOOPBACK=false
```

If the customer uses another trusted Nextcloud HTTPS origin, set both origin and collection consistently. Set the **container** paths above, not workstation paths. Keep the same project name/configuration ID/calendar identities/state volume on every start. A different namespace creates independent identities/copies; it is not an error-recovery mechanism.

### Build and set the image

An image is built locally from the approved checkout; no registry push is needed when building and running on the same server. In the same shell used to select CUSTOMER_SYNC_COMMIT, verify HEAD and build a tag containing that exact SHA:

```sh
test "$(git rev-parse HEAD)" = "${CUSTOMER_SYNC_COMMIT:?Select the approved SHA in section 4 first}"
docker build -t "calendar-sync:customer-${CUSTOMER_SYNC_COMMIT}" .
docker image inspect "calendar-sync:customer-${CUSTOMER_SYNC_COMMIT}" --format '{{.Id}}'
printf 'CUSTOMER_SYNC_IMAGE=calendar-sync:customer-%s\n' "$CUSTOMER_SYNC_COMMIT"
```

Copy the printed CUSTOMER_SYNC_IMAGE line into .env, replacing any existing entry so there is **exactly one**. Keep SYNC_DRY_RUN=true for preview. The image tag contains the selected source SHA; the separately printed image ID identifies the actual built artifact. In a new shell the exported commit variable will not persist: recover the approved SHA from the deployment record and verify HEAD before rebuilding. Create a local compose.customer.yaml with this reviewed override:

```yaml
services:
  worker:
    image: ${CUSTOMER_SYNC_IMAGE:?Set CUSTOMER_SYNC_IMAGE in .env}
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

The base Compose safety settings remain: UID10001, read-only root, private mounted credentials, persistent named state, tmpfs, init, dropped capabilities, no-new-privileges, 120-second stop grace and restart unless stopped. No new public port is published. Log rotation does not implement database audit retention.

Define this helper in each new server shell from /opt/calendar-sync:

```sh
customer() {
  docker compose --env-file .env -p calendar-sync-customer \
    -f compose.yaml -f compose.customer.yaml "$@"
}
customer config --quiet
customer config --images
```

The image listing must show the exact calendar-sync:customer-<approved-SHA> tag you built. An unset image setting or mismatched tag must be fixed before starting the service. Do not commit .env, customer-specific identifiers/paths, credentials, local overrides, image manifests or database files to the public repository.

## 7. Validate without writing events — CUSTOMER-UAT-07

Confirm all REPLACE fields are gone and SYNC_DRY_RUN=true, then:

```sh
customer run --rm --no-deps worker calendar-sync worker --once
echo "Preview exit code: $?"
```

Expected dry_run true and a healthy complete preview with exit0. Compare proposals against the customer's actual supported source inventory. Unlike the two-event UAT, production has no fixed expected count. All-zero is not proof of correctness unless the intended source is genuinely empty. Investigate unexpected unsupported/missing/conflict counts before activation. No destination events should change. The executor re-reads source before each upload; a large initial calendar can be API-intensive. Review expected volume/quota and monitor the initial rollout; no customer throughput benchmark is implied.

For a failed cycle, stop the worker and isolate the provider reads without event writes:

```sh
customer stop worker
customer run --rm --no-deps -T worker python - <<'PY'
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

The script may refresh the token, but it never executes sync or writes calendar events. Do not switch to localhost or disable HTTPS to fix production connectivity. Google API disabled/scope/admin/calendar permissions and Nextcloud login/collection/write rights are separate issues. Never display credentials or raw upstream errors in support logs. After repairing credentials/connectivity, repeat the dry-run procedure and explicitly recreate the service in the reviewed mode; it remains stopped until you do so.

## 8. Activate live sync and verify restart — CUSTOMER-UAT-08

After customer/operator review of the preview and supported limitations, change only SYNC_DRY_RUN=false in .env. Recreate explicitly so the running container receives the intended settings:

```sh
customer up -d --no-build --force-recreate worker
customer logs -f --tail 20 worker
```

Expect applied actions for supported changes, then unchanged results on later cycles; no unexpected conflict/pending outcomes. Ctrl-C leaves the log viewer only. Verify supported event date/time/all-day boundaries and exactly one managed copy per source in Nextcloud; review source omissions against agreed requirements. Google must remain unchanged. Existing unowned destination events remain untouched.

```sh
customer exec worker calendar-sync health
echo "Health exit code: $?"
customer restart worker
customer logs --since 2m worker
customer ps
```

Wait for a newly completed post-restart cycle, not merely an older log line. Expect retained state, no duplicates and health0 after completion. Initial or stale health can return1 until a valid cycle; healthy dry-run never proves applied synchronization. Docker does not restart a running process only because its health turns unhealthy. Alert on persistent failed/stale/conflicted/pending cycles.

To return to observation-only operation: stop worker, set SYNC_DRY_RUN=true in .env, then `customer up -d --no-build --force-recreate worker`. Editing the file alone does not change an existing container. To stop cleanly: `customer stop worker`, then `customer ps -a` should show Exited(0). Preserve all state/credential volumes. Never use down --volumes or a new configuration ID as a restart/retry shortcut.

### Run continuously after customer acceptance

Once the customer acceptance checks are complete and the customer has approved live operation, keep the same approved image, project name, configuration/calendar identities, credential paths and state volume used for acceptance. On the **customer server**, edit /opt/calendar-sync/.env so it contains:

```dotenv
SYNC_DRY_RUN=false
```

From a new SSH shell, define the helper again and recreate the service in detached mode so it receives the live setting:

```sh
cd /opt/calendar-sync
customer() {
  docker compose --env-file .env -p calendar-sync-customer \
    -f compose.yaml -f compose.customer.yaml "$@"
}
customer config --quiet
customer config --images
customer up -d --no-build --force-recreate worker
```

Confirm the image listing matches the recorded approved tag before starting. If operating a deliberately reviewed rollback, include its override last as described below; do not accidentally reselect the newer image. Detached mode keeps the worker running when you close SSH. Changing .env alone does not update an existing container; recreation is required when changing its settings.

Verify a newly completed live cycle before leaving:

```sh
customer ps
customer logs --tail 20 worker
customer exec worker calendar-sync health
echo "Health exit code: $?"
```

Expect a running worker, dry_run false, healthy cycle status and health exit0 after completion. Initially it may be starting. Inspect unexpected conflict/pending/skipped counts against the accepted inventory rather than treating health alone as proof that every event/field synced. With default SYNC_POLL_SECONDS=300, it waits roughly five minutes plus jitter after a cycle; actual cycle-to-cycle timing also includes work duration. Failures use the documented backoff/provider cooldown.

The base Compose restart:unless-stopped policy restarts the process after failure and can bring it back after Docker starts, provided the container was not explicitly stopped. **Docker itself must start automatically after a server reboot.** On a systemd Linux server, check:

```sh
systemctl is-enabled docker
```

Expected enabled. If disabled/masked or the host uses another service manager, have the customer administrator configure/verify Docker auto-start through the host's normal operating procedure. Do not assume reboot recovery solely from a healthy container. An unhealthy status alone does not restart a still-running worker; monitoring/alerts remain necessary.

For maintenance, stop cleanly and retain state:

```sh
customer stop worker
customer ps -a
```

Expect Exited(0) after graceful shutdown. An explicitly stopped service stays stopped until you resume it; after maintenance, repeat the detached startup and new-cycle health checks above. Do not delete the state/credential volumes or change project/configuration IDs to restart. If choosing observation-only operation, set SYNC_DRY_RUN=true and recreate instead of silently leaving live mode enabled.

## 9. Backup, recovery and handover — CUSTOMER-UAT-09

Assign a backup/alert owner and organization-appropriate recovery-point, retention and secret-storage policies. Take a verified state snapshot before upgrades/credential-path relocation and on the agreed schedule; transfer protected copies to independent encrypted storage. A backup on the same Docker volume is not disaster recovery. Keep the compatible image ID/commit and private configuration references with the manifest.

Once the reviewed snapshot helper is present in the approved checkout, a quiesced default-path backup is:

```sh
customer stop worker
chmod 755 scripts
chmod 644 scripts/state_snapshot.py
customer run --rm --no-deps --volume "$PWD/scripts:/checks:ro" worker \
  python /checks/state_snapshot.py backup \
  --source /data/calendar-sync.sqlite3 \
  --destination /data/backups/REPLACE_UNIQUE_BACKUP_NAME.sqlite3
```

Use a new filename every time. The chmod commands apply only to public helper code so UID10001 can read the read-only bind mount even if the checkout was created with a restrictive umask; do not apply them to secrets/state. Stop every client; locks, unsafe permissions, sidecars, schema drift or existing destinations must be investigated, not bypassed. The helper's source and destination lock/reservation files are intentionally retained. A failed command/file is not a verified backup. Restore only to a new protected path with a compatible application and a reviewed dry-run, as specified in [operations.md](operations.md#restore-drill-and-rollback--ops-uat-04).

For a **routine backup only**, after a verified PASS and with no upgrade/restore work pending, resume the existing deployment and verify a newly completed cycle:

```sh
customer up -d --no-build worker
customer logs --since 2m worker
customer exec worker calendar-sync health
echo "Health exit code: $?"
```

Expect a new healthy cycle and health0 after completion. An explicit stop is not undone by restart:unless-stopped; skipping this step leaves synchronization stopped. For upgrades, failures or restore work, keep the service stopped until the reviewed recovery steps are complete.

### Customer-specific restore and application rollback

Use the safety rules from operations.md, but **keep the customer Compose context** below. Its generic `prod` examples omit this deployment's project/image override and would select different state. Preserve a verified snapshot of current/post-failure state first; restoring an old snapshot does not undo remote writes. With every worker stopped, restore the chosen verified backup to a fresh path in the same customer volume:

```sh
customer run --rm --no-deps --volume "$PWD/scripts:/checks:ro" worker \
  python /checks/state_snapshot.py restore \
  --source /data/backups/REPLACE_VERIFIED_BACKUP_NAME.sqlite3 \
  --destination /data/recovered/REPLACE_FRESH_RESTORE_NAME.sqlite3
```

After PASS, set SYNC_STATE_FILE in .env to the exact new destination path and SYNC_DRY_RUN=true; keep the same configuration/calendar IDs and runtime credential references. Recreate with `customer up -d --no-build --force-recreate worker`, inspect the complete preview and retain all old files/volumes. Only deliberately resume live mode after remote divergence/pending-intent review. No restore command overwrites current state or automatically adopts destination events.

An application rollback requires a preserved compatible previous worker image and its matching pre-upgrade snapshot; neither is assumed to exist. If they were recorded, restore that snapshot to a new path and select the previous image using an override applied **after** compose.customer.yaml:

```sh
docker image tag "${CUSTOMER_ROLLBACK_IMAGE_ID:?Set the recorded compatible previous image ID}" calendar-sync:customer-rollback-reviewed
```

Create a reviewed local compose.customer-rollback.yaml containing `services: {worker: {image: 'calendar-sync:customer-rollback-reviewed'}}`. With .env pointing to the matching restored copy and dry-run true:

```sh
docker compose --env-file .env -p calendar-sync-customer \
  -f compose.yaml -f compose.customer.yaml -f compose.customer-rollback.yaml \
  up -d --no-build --force-recreate worker
```

Retain the same project/volume names and all evidence. Never run an older image against a newer schema, lower user_version, build/pull an unrecorded replacement or delete state to make rollback pass. For subsequent inspection/control during rollback, use the same three-file Compose invocation (or redefine the customer helper with the rollback override last); the original two-file helper would otherwise reselect the newer image on its next up command.

Restoring SQLite does not undo Nextcloud writes; old snapshots can lose mappings/operation evidence. Preserve post-failure state too. Do not fabricate ETags/mappings, change IDs, reset attempts, delete edited destination events or force unconditional PUT to clear conflicts. Source deletions/cancellations do not delete destination copies. Retention purge/export is not implemented: keep application history/pending intents until a separately reviewed migration/policy exists; log rotation does not authorize SQL deletion or Docker-volume pruning.

Renew Google authorization deliberately on the workstation if revoked/expired, then securely replace only the runtime token with private ownership while all clients are stopped; retain references/state. Rotate the dedicated Nextcloud device password similarly and revoke the old token only after the replacement is accepted. If runtime credential paths must move, back up and use the scoped, compare-and-swap procedure in [operations.md](operations.md#scoped-credential-path-relocation--ops-uat-05), never delete/recreate the configuration.

## Customer acceptance record

| Case | Required evidence |
| --- | --- |
| CUSTOMER-UAT-01 | Customer accepted one-way/single-event/omission/no-delete/conflict behavior; named operator and backup/alert owner. |
| CUSTOMER-UAT-02 | Customer-owned Workspace project, correct audience/client, API/admin/read-scope/calendar access and completed workstation authorization. |
| CUSTOMER-UAT-03 | Exact private HTTPS collection, correct account/app password and destination read/write permissions; empty initial destination or reviewed inventory. |
| CUSTOMER-UAT-04 | Actual server OS/architecture/Docker/Compose, approved main SHA and image ID recorded. |
| CUSTOMER-UAT-05 | Secure transfer, UID10001/private file/directory permissions, writable token parent/read-only password mount and protected staged-copy handling. |
| CUSTOMER-UAT-06 | Private settings, stable state/identity, exact tagged image and Compose validation; no HTTP/UAT networking. |
| CUSTOMER-UAT-07 | Complete dry-run with reviewed actual inventory and no destination writes, including unsupported/missing/empty-source checks. |
| CUSTOMER-UAT-08 | Explicit live decision, correct Nextcloud copies, repeat/restart without duplicates, detached continuous operation, Docker auto-start policy, health0 and graceful stop/observation-mode recovery. |
| CUSTOMER-UAT-09 | Verified backup and isolated restore drill, independent storage/image compatibility, retention/alert plan and conflict/token-rotation procedures understood. |

Record each case using [test-evidence.md](test-evidence.md), including tested SHA, local overrides, account/setup path, date, expected vs actual counts and cleanup. All customer-environment cases are **Not run by the guide author**; no customer server, real credentials, deployment or production backup/restore was accessed/performed while writing it. Prior isolated UAT logs are useful regression evidence, not customer sign-off. Complete outstanding #14 release acceptance and required owner review before declaring production readiness.
