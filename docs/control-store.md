# Owner, vault and migration foundation — issue #33

Development version **0.2.0.dev0**, not the frozen v0.1.0 customer image. This page covers the local backend library and offline operator tools from #33. The separate [web login shell](web-login.md) now authenticates/read-displays owned records; scheduler integration, provider connection, configuration UI and two-way sync remain unavailable. The existing worker still uses its original settings/engine database; it does not consume these new records. Do not point production at a new control database expecting multi-user synchronization. Use disposable state for acceptance below.

## Implemented boundary

ControlStore owns a separate application-tagged, versioned SQLite schema (control v1). Engine schema remains v2 and is not changed. Normal control handles share a maintenance lock and serialize mutations using BEGIN IMMEDIATE; a maintenance handle excludes all other cooperating control handles. The engine still uses its own lifetime exclusive .lock. One service process will own the control repositories; worker job/assignment and service authentication arrive in #34.

OwnerContext requires exact issuer + subject, never email. Its constructor and register_owner are **trusted backend/operator interfaces**, not JWT validation or HTTP authentication. #19 must construct contexts only after verified Keycloak login. Do not expose these methods directly as routes. Disabled or unknown owners cannot access repositories. Foreign and missing connection/pair IDs yield the same control_not_found code; association checks apply when creating pairs. Lists and bounded security-audit pages are owner-scoped. Raw connection, SQL and Vault objects are internal implementation details, not serializers.

Connections store verified provider account identity supplied by a trusted connector, revision, lifecycle and encrypted bytes. Public connection projections omit ciphertext, vault references and paths. resolve_credentials is a trusted connector-only method, not a downloadable-token API. It rejects disconnected/legacy-unverified credentials. No Google/Nextcloud identity, scope, grant or network validation is claimed by this library; #35/#36 provide those boundaries. Local disconnect does not revoke a provider grant. The current foundation has no enabled pair, so disabling owners/connections cannot accidentally leave a new scheduler running.

Vault uses the already locked cryptography runtime dependency, now declared directly. AES-256-GCM uses a fresh 12-byte random nonce and associated connection ID, owner ID, provider, credential kind and revision. Ciphertext/key-check tampering or a wrong key fails closed. Credential replacement is revision-checked in the same transaction; a stale refresher cannot overwrite a newer value. Audit stores fixed action names/timestamps only, is append-only, and never receives secret payloads. [AES-GCM reference](https://cryptography.io/en/stable/hazmat/primitives/aead/).

The raw 32-byte vault key lives in a different private directory from the database. It is never inserted into SQLite, images, env values or backup bundles. Files must be current-UID owned, private (0600 or stricter), single-link regular files; immediate directories 0700. Symlinks/untrusted writable ancestors are refused (OS-managed root aliases such as macOS /var are resolved). POSIX Linux/macOS/WSL Linux filesystems only; Windows Python, /mnt/c secret storage, network filesystems and same-UID hostile file replacement are unsupported. Trusted operating-system administrators can access process memory; encryption at rest is not protection against a compromised running service.

Pairs reserve two backend-supplied canonical endpoint identities atomically, globally across owners and connection aliases. The expected identity namespaces are google: and nextcloud:. Quarantined legacy configurations reserve their known endpoint identities too; explicit later assignment transfers those reservations atomically. The library does **not** discover/canonicalize arbitrary browser URLs: connector proof/discovery is a gate in #36/#37, including resolving legacy shared-calendar aliases before activation. New pairs are paused, dry-run and Google-to-Nextcloud only. Archiving is revision-checked and retains both reservations and all audit. No activate/delete/retarget API exists. A historical endpoint cannot be silently reused to create duplicate copies.

## Credential-free acceptance — CTRL-UAT-01–06

Prerequisites: this feature checkout on Linux/macOS/WSL, Python 3.12+ and dependencies installed. Run from the repository root; **no real accounts, key files or calendars are required**:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
python -m unittest discover -s tests -p 'test_control*.py' -v
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
python scripts/check_runtime_lock.py
python -m pip check
python scripts/smoke_control.py
```

Expected: **28 focused tests**, **253 total tests** individually catalogued, 35 runtime pins and metadata match, no broken dependencies. Focused executable cases and subcases are CTRL-001–016 and CMIG-001–012 in [the catalogue](test-cases.md). The smoke script prints six PASS lines plus the final cleanup/no-network confirmation. It creates only synthetic credentials and local fixtures in an automatically removed temporary directory; it never reads customer secret files or contacts providers. Assertion/fixed-code failure is a failed check; stop and retain redacted output, do not edit production state to make it pass.

| Case | Action inside the reproducible script | Expected observable result |
| --- | --- | --- |
| CTRL-UAT-01 | Generate disposable key/store and register synthetic Alice/Bob issuer/subjects. | Private state initialized; owner records explicit, no browser or provider request. |
| CTRL-UAT-02 | Save synthetic credentials; resolve Alice's connection as Bob; inspect safe projections/SQLite for plaintext sentinel. | Generic denial for Bob; synthetic payload absent from DB/projections, Alice alone can resolve it. |
| CTRL-UAT-03 | Create, then archive a pair; attempt to reuse one of its canonical endpoints. | Paused/dry-run initial state, reservation collision refused even after archive. |
| CTRL-UAT-04 | Preview/import legacy fixture assigned only to Alice; repeat import and compare engine dump. | Preview rolls back control records, apply is explicit/idempotent, other config quarantined, engine mapping/owner markers unchanged. |
| CTRL-UAT-05 | Hold maintenance/engine locks, snapshot both stores, rotate to a new key. | Completed authenticated bundle, atomic rotation, same decrypted synthetic credentials. Old key no longer opens current control store. |
| CTRL-UAT-06 | Restore pre-rotation bundle into new isolated state using original key; try wrong key. | Wrong-key refusal, correct-key recovery of both stores and credentials, source preserved; temporary files cleaned at script end. |

Author local synthetic results are recorded in the PR with exact SHA/runtime; owner execution must be recorded separately using [test-evidence](test-evidence.md). Real Keycloak login, provider connection/revocation and customer migration: **Not run**, corresponding service/auth integrations do not exist in this PR. Synthetic tests do not prove tenant policy, production backup storage or live account isolation.

For an offline non-root Docker exercise (build needs registry access; execution has no network):

```sh
docker build --build-arg VCS_REF="$(git rev-parse HEAD)" -t calendar-sync:control-foundation .
docker run --rm --network none --read-only --tmpfs /tmp:rw,mode=1777 \
  --mount "type=bind,src=$PWD/scripts,dst=/checks,readonly" \
  calendar-sync:control-foundation python /checks/smoke_control.py
docker run --rm --network none --read-only --tmpfs /tmp:rw,mode=1777 \
  --mount "type=bind,src=$PWD/tests,dst=/app/tests,readonly" \
  --mount "type=bind,src=$PWD/examples,dst=/app/examples,readonly" \
  --mount "type=bind,src=$PWD/scripts,dst=/app/scripts,readonly" \
  --mount "type=bind,src=$PWD/docs,dst=/app/docs,readonly" \
  calendar-sync:control-foundation python -m unittest discover -s tests -v
```

Expected same six smoke results and 253 passing tests under UID10001/Python3.12. Containers remove themselves and temporary fixtures; the local development image remains until deliberately removed. Do not tag this as v0.1.0 or mount customer state/secrets for this exercise.

## Offline operator CLI — CTRL-UAT-07

**For acceptance testing, run this complete example first. It creates every input automatically and contains no placeholders:**

```sh
python scripts/smoke_control_cli.py
```

Expected PASS CTRL-UAT-07 and PASS CTRL-UAT-08 followed by cleanup confirmation. It invokes the actual operator CLI in subprocesses, exercises preview/apply/retry, snapshot/restore/rotation and negative paths, and verifies unchanged synthetic engine state. No Keycloak server, user credentials, customer files or pre-existing stage is required. It cleans its own temporary directory and leaves .local/control-stage untouched. The ordinary 253-test suite is unchanged; this is an additional reproducible acceptance exercise, also run in CI.

The deployment-specific templates **below are not directly executable test fixtures**. Every REPLACE value requires an actual reviewed path/identity before use. A register-owner result means only that a row was stored; it does not prove Keycloak validation. A literal REPLACE issuer/subject can therefore be stored as a test row, but must never be used to assign real customer configurations. For synthetic acceptance, use the script above rather than adapting customer paths.

First practice this with copied/disposable state. All commands below are for a future staged migration, not instructions to cut the running customer over to a GUI that does not exist yet. Use the same trusted OS account that owns the engine/credentials; container deployments normally use UID10001. Stop **every** engine/control client, verify existing engine backup with [operations](operations.md), and work on an isolated engine copy first. The CLI refuses hot journals/sidecars, wrong schema and active locks. It does not bypass or migrate the engine.

Create a separate protected control directory and key directory. Replace example paths with reviewed deployment-specific paths; never replace a working key by rerunning init-key. The first command creates the key privately; subsequent commands require it:

```sh
umask 077
mkdir -p .local/control-stage/data .local/control-stage/keys
chmod 700 .local/control-stage .local/control-stage/data .local/control-stage/keys
python -m calendar_sync.control_admin \
  --key-file "$PWD/.local/control-stage/keys/vault.key" init-key
python -m calendar_sync.control_admin \
  --key-file "$PWD/.local/control-stage/keys/vault.key" \
  --database "$PWD/.local/control-stage/data/control.sqlite3" init
```

Expected key_created=true, then control_schema=1. Repeating init-key must fail with control_key_creation_failed and leave the original key intact. Wrong/missing/broadly readable key must fail without printing its content/path. There is no recovery from loss of the only key: back it up through the customer's approved separate secret-storage process before storing real credentials.

Independently verify the target Keycloak realm issuer and user subject with the administrator; there is **no online verification in this operator command**. Provision only that explicit identity:

```sh
python -m calendar_sync.control_admin \
  --key-file "$PWD/.local/control-stage/keys/vault.key" \
  --database "$PWD/.local/control-stage/data/control.sqlite3" \
  register-owner --issuer 'https://REPLACE_KEYCLOAK/realms/REPLACE_REALM' \
  --subject 'REPLACE_VERIFIED_SUBJECT'
```

Expected a server-generated owner_id; repeating the same issuer/subject returns the same ID. Put that ID in a private assignments.json mapping exact engine configuration IDs to owner IDs, for example {"customer-master":"REPLACE_OWNER_ID"}. Omitted IDs or null assignments are quarantined, not exposed to any user. Duplicate JSON keys, unknown configuration IDs, unknown/disabled owners and reassignment of an already imported configuration are refused. The manifest itself must be private (0600 in a 0700 directory).

Preview (replace engine/manifest paths, use **an isolated verified engine copy**, not a live database):

```sh
python -m calendar_sync.control_admin \
  --key-file "$PWD/.local/control-stage/keys/vault.key" \
  --database "$PWD/.local/control-stage/data/control.sqlite3" \
  import-legacy --engine-file '/REPLACE_PRIVATE_STAGE/engine.sqlite3' \
  --assignments '/REPLACE_PRIVATE_STAGE/assignments.json'
```

Expected aggregate configurations/assigned/quarantined counts and applied=false; no engine writes and all control plan changes rolled back. After verifying counts/identities, run the same command with **--apply** appended: expected applied=true. Repeat must be idempotent. Quarantined configurations can later be assigned explicitly by rerunning the complete manifest with the same unchanged engine snapshot. Assigned owners cannot silently change. The importer wraps all control changes in one transaction while holding the unchanged engine lock; no distributed transaction or cross-database write is required.

Engine configuration IDs, owner='operator' markers, calendar IDs, UIDs, mappings, audit and pending intents remain exactly as they were. Legacy credential file paths are encrypted as compatibility references; no credential file is opened or copied. Imported connections are needs_verification and their pairs paused/dry-run with legacy_review=1. Path aliases, real ownership/permissions and pending intents need connector/scheduler review before activation in a future issue. An engine changed since import causes control_engine_changed_requires_review; do not delete the ledger/digest or reimport with a new namespace to bypass it.

Cleanup for this manual stage: retain key/state through the reviewed recovery policy if real data was used; do not delete production grants/mappings or resume from an old engine snapshot. For purely synthetic isolated copies, remove only your recorded stage after verifying it contains no production material. The six-case smoke script above performs its own disposable cleanup.

## Backup, restore and rotation — CTRL-UAT-08

For a complete disposable CLI exercise of this case, run python scripts/smoke_control_cli.py as above. The following are operator templates for reviewed real paths, not additional commands required after the synthetic PASS output.

Stop all clients and use a new backup directory every time. Snapshot requires the maintenance control lock and quiesced engine lock; it refuses engine/control divergence. Replace every path below with the reviewed staged paths:

```sh
python -m calendar_sync.control_admin \
  --key-file '/REPLACE_KEYS/vault.key' --database '/REPLACE_CONTROL/control.sqlite3' \
  snapshot --engine-file '/REPLACE_ENGINE/engine.sqlite3' \
  --destination '/REPLACE_PRIVATE_BACKUPS/unique-bundle'
python -m calendar_sync.control_admin \
  --key-file '/REPLACE_RECOVERED_KEYS/vault.key' \
  restore --source '/REPLACE_PRIVATE_BACKUPS/unique-bundle' \
  --destination '/REPLACE_PRIVATE_RESTORE/new-isolated-directory'
```

Expected control_schema=1/engine_schema=2. Bundle contains control.sqlite3, engine.sqlite3 and manifest.enc only, each private. The AES-GCM authenticated manifest binds both SHA256 file hashes, engine logical digest, format and key identity. SQLite backup/integrity checks run before completion. The **engine snapshot still contains its normal plaintext event identities and credential references**: the whole bundle needs protected/encrypted backup storage. The vault protects credential payloads, not all engine metadata. Recover the matching vault key separately; no key is copied into the bundle. Wrong key, modified bytes, incomplete manifests and existing restore destinations are refused. Restore never replaces the source/current files and does not undo remote writes. New failed destinations created by this operation are cleaned up; existing destinations are left alone.

While stopped, create a new key at a **new** private filename, then rotate:

```sh
python -m calendar_sync.control_admin --key-file '/REPLACE_KEYS/new-vault.key' init-key
python -m calendar_sync.control_admin \
  --key-file '/REPLACE_KEYS/old-vault.key' --database '/REPLACE_CONTROL/control.sqlite3' \
  rotate-key --new-key-file '/REPLACE_KEYS/new-vault.key'
```

Expected key_rotated=true. All ciphertext/key check updates commit together; any authentication/encryption failure rolls them all back. Update the deployment's external key-file reference before reopening; the old key must no longer open the rotated current store. **Older bundles still require the old key**; retain protected versioned key recovery material for their retention period. Do not delete the only old key as rotation cleanup. Newer schema is refused, never downgraded. Locks are retained as coordination files; do not remove them to bypass contention.

Hot control journals/sidecars after a crash are conservatively refused. Stop all clients, preserve database **and** sidecars plus key, and perform reviewed SQLite recovery on an isolated copy before retrying. Never delete a journal to force startup. Automatic crash-recovery tooling for the future scheduler/control deployment is not provided here. Backup verification establishes local consistency, not reliable external backup storage or live production acceptance.
