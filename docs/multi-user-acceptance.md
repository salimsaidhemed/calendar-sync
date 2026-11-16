# Multi-user design review and future acceptance

Issue #17 is a design-only delivery. Current runtime remains v0.1.0; do not try the proposed /api/v1 endpoints against a customer deployment. No live login, discovery, migration or two-way feature is implemented here. Required executable scenarios AUTHZ-01 through TWO-01 in [the design](multi-user-design.md#threat-to-test-requirements) become automated cases under their implementation issues; they are not falsely counted as current unit tests.

## Regression commands

Prerequisites: Linux/macOS, Python 3.12+, reviewed feature checkout and installed project dependencies. From the repository root:

```sh
. .venv/bin/activate
python scripts/check_runtime_lock.py
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
python -m pip check
git diff --check
```

For a fresh environment first run python3 -m venv .venv, activate it, then python -m pip install -r requirements.lock and python -m pip install --no-deps -e . as in [testing](testing.md). Expect 19 installed pins/version match, 216 tests exactly catalogued and passing, no broken requirements or whitespace errors. Existing tests use fakes/temporary state and clean up automatically. The original #17 PR added no tests mirroring prose. #33 now adds 28 meaningful control/vault/migration tests; they establish backend foundation behavior, not implemented GUI/OIDC authorization. See control-store.md for its current acceptance exercise.

## Design review cases

Prerequisites: read multi-user-design.md and the linked implementation issues; no provider account, Keycloak deployment, secret or event fixture is needed. Review source StateStore lifetime locking, worker owner='operator', and existing provider limitations for compatibility. Record reviewer/date/commit and per-row Passed/Failed/Not run in [test-evidence](test-evidence.md); no state changes or cleanup are required. Author review does not replace owner acceptance.

| Case | Reviewer steps | Expected result |
| --- | --- | --- |
| GUI-DESIGN-01 | Trace Alice/Bob from Keycloak login through connection, discovery, pair, job and history; repeat with same subject/different issuer and guessed IDs. | Every hop has validated owner context, no email-based ownership, generic foreign-ID denial and AUTHZ-01 implementation gate. |
| GUI-DESIGN-02 | Trace a request, scheduled cycle, provider write and report; inspect who opens each database; inject worker crash/report outage on paper. | Web never opens engine StateStore; one engine lock, durable commands/intents/idempotent reporting; no unsafe lease failover. |
| GUI-DESIGN-03 | Review Google/Nextcloud browser flows, refresh, expiry/replay/CSRF cases and worker secret resolution. | No server/browser-installed Python requirement, manual token download, account password storage or token exposure; browser consent separate from unattended service operation. |
| GUI-DESIGN-04 | Pair several calendars, then share a calendar between Alice/Bob and request overlapping pairs through different aliases. | Exact provider identities, complete discovery/privileges and atomic global reservations; unresolved aliases block activation, private collision response, no title-based adoption. |
| GUI-DESIGN-05 | Inspect lifecycle/HTTP tables for stale edits, stale preview, pause/archive, sync-now, unsupported two-way and existing destination events. | Explicit revisions, dry-run/paused defaults, provider revalidation, in-flight drain acknowledgement and no deletes/implicit escalation. |
| GUI-DESIGN-06 | Trace migration of one legacy operator configuration with pending intents plus one unassigned configuration; consider partial import/rollback. | Explicit owner manifest; unchanged engine IDs/markers/history, quarantine, stopped services, backup and reconciled control/engine state; no live rollback from an old snapshot. |
| GUI-DESIGN-07 | Trace wrong issuer/token, cross-owner callback, malicious Nextcloud href, service endpoint accessed using browser cookie and wrong vault key. | All deny without leaking credentials/event bodies; required AUTHN/SESSION/NETWORK/SECRET tests and operational key/egress prerequisites are explicit. |
| GUI-DESIGN-08 | Compare roadmap, issues #33–#38 and GUI #19/#18 against user flow and v0.1.0 limitations. | Implementable dependency order; GUI one-way first, ordinary two-way later; recurrence/deletes separately gated; no Java/rewrite or claim GUI already exists. |

## Future runtime acceptance (Not run: feature not implemented)

Author design-review record, 2026-11-16: GUI-DESIGN-01–08 were reviewed against the proposed contract and the current state/worker boundaries; Passed as document consistency checks. Owner acceptance: Not run, awaiting PR review. These results do not establish that any proposed runtime authentication, isolation, scheduling or migration behavior exists. Automated regression results belong in the PR with its exact commit and runtime; future live cases below all remain Not run.

| Case | Required disposable environment / user actions | Expected result and cleanup |
| --- | --- | --- |
| GUI-LIVE-01 | Two Keycloak test users; separate Google Workspace and Nextcloud test accounts with explicit grants. Alice connects providers in laptop browser while server runs headless; repeat with Bob. | Connections visible only to owner, consent/cancellation/expiry clear, no downloaded token. Disconnect disposable grants deliberately; never revoke production grants as cleanup. |
| GUI-LIVE-02 | Each user has two disjoint Google and Nextcloud test calendars; create explicit ordinary timed/all-day fixtures with date/timezone/exclusive-end controls from existing acceptance guides. Map two pairs each, review complete previews, enable selected writes. | Only selected calendars receive supported events; other users cannot read/change mappings/history; no unexpected invitations/deletes. Preserve acceptance evidence and stop/pause all disposable pairs afterward. |
| GUI-LIVE-03 | Close browser, restart worker, interrupt network and restore it, then reopen GUI. Include stale session and revoked test connection. | Persisted jobs resume without duplicates; stale status distinct from healthy; revoked connection pauses; no new writes on stale authorization. Restore only test connectivity/grants and leave pairs paused/dry-run. |
| GUI-LIVE-04 | After #38 only: use new empty calendars and ordinary fixtures created on each side, then update same mapped event on both sides before next cycle. | Both directions work once, no echo/duplicates; simultaneous edits conflict and remain unmodified pending review. Recurrence/deletes stay skipped. Leave paused and retain engine state; no SQL reset or forced overwrite cleanup. |

Implementation PRs must replace these high-level live templates with exact full commands/UI fixture controls, actual case IDs, account-class coverage, failure expectations and safe cleanup for the shipped feature. Do not mark live cases Passed from CI, a design review or merge status.
