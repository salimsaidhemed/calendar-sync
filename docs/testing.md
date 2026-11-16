# Testing and review procedure

Start with this page for every issue and PR. A passing CI run is automated evidence; owner acceptance requires understandable, repeatable user scenarios as well. Do not mark a feature accepted merely because its PR merged.

## Environment and commands

Use Linux/macOS, Python 3.12+ and the intended review branch. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
python -m pip check
```

Expected: catalogue check reports 247 tests documented exactly once; full suite reports 247 tests and `OK`; no broken dependencies. The [catalogue](test-cases.md) gives focused commands, conditions and expected results for every automated case. Package installation requires registry access; the tests use fakes/temporary files and require no Google or Nextcloud credentials. Do not edit secret files to make a test pass.

To copy a Python example into zsh/bash, include the opening `python - <<'PY'` and closing unindented `PY`. Python statements pasted directly into the shell produce shell parse errors. Copy the complete block, not just its middle. Activate the virtual environment and export the documented script variables in the same shell.

## Case types and evidence

| Case family | Purpose | Where to find steps | Evidence required |
| --- | --- | --- | --- |
| CFG / STATE / OAUTH / GCAL / NORM / DAV / PLAN / EXEC / JOURNAL / WORK / SNAP / DOCOPS / PKG / CTRL / CMIG / WEB / OIDC | Each automated case, including negative paths | [247-case catalogue](test-cases.md) | Test identifier, command, runtime, result and tested commit. |
| UAT-01 through UAT-12 | User-visible local/Google behavior | [Manual case matrix and procedures](google-acceptance.md) | Individual case/subcase result; separate real-provider results from fake tests. |
| NC-UAT-01 through NC-UAT-07 | Offline normalization and explicit Nextcloud reads | [Nextcloud acceptance](nextcloud.md#acceptance-cases) | Fixture controls, safe counts/reasons, permissions restored; live checks separate from fakes. |
| PLAN-UAT-01 through PLAN-UAT-04 | Pure planning, conflicts and read-only provider previews | [Planner acceptance](planner.md#acceptance-cases) | Action counts/reasons, no applied-state changes, live reads separately evidenced. |
| EXEC-UAT-01 through EXEC-UAT-07 | Conditional execution, idempotency, conflicts and crash recovery | [Execution acceptance](execution.md#acceptance-cases) | Separate fake recovery evidence from disposable live write/read acceptance; preserve stable state. |
| WORK-UAT-01 through WORK-UAT-07 | Polling, health, restart, signals, lock contention and Compose | [Worker acceptance](worker.md#user-acceptance-cases) | Mode/counts/exit status, signal results and state retained; live tests separately evidenced. |
| GOV-UAT-01 through GOV-UAT-04 | Current GitHub rules and reviewer identity constraints | [Protection verification](branch-protection.md) | Read-only effective-rule inspection; independent owner approval remains unresolved under #8. |
| OPS-UAT-01 through OPS-UAT-06 | Authorization/deployment, quiesced backup, isolated restore, path relocation and retention review | [Operations runbook](operations.md) | Exact image/schema/state identities, synthetic restore/rollback evidence; production results separate. |
| DOCKER-UAT-01 through DOCKER-UAT-05 | Reproducible isolated Docker fixture setup, credentials, diagnostics/renewal and live restart | [Docker UAT](docker-uat.md) | New two-event source variant, independent volumes and explicit visual/cleanup results; prior user evidence separately scoped. |
| CUSTOMER-UAT-01 through CUSTOMER-UAT-09 | Customer Workspace production preparation, approved source/image, credentials, preview/live and handover | [Customer deployment](customer-production-deployment.md) | Actual customer OS/architecture/account/SHA/image, private credential setup and signed-off inventory/restart/backup results; author has not deployed to a customer. |
| REL-UAT-01 through REL-UAT-04 | Runtime/version freeze, candidate artifact, truthful acceptance and owner-merged publication | [Release procedure](release.md) | Exact approved source/image/pin identity; candidate checks separate from actual tag/publication and customer go-live. |
| GUI-DESIGN-01 through GUI-DESIGN-08 | Multi-user API/authorization, scheduling, credential and migration design review | [Multi-user review](multi-user-acceptance.md) | Design-only checks; GUI-LIVE-01–04 are future templates, Not run until corresponding implementation ships. |
| CTRL-UAT-01 through CTRL-UAT-08 | Owner/control/vault, explicit import, paired backup/restore and key rotation | [Control foundation](control-store.md) | Six credential-free synthetic exercises plus operator stage/recovery instructions; no live GUI/provider acceptance implied. |
| WEB-UAT-01 through WEB-UAT-06; WEB-LIVE-01 through WEB-LIVE-04 | Signed offline login/session exercise and separate real Keycloak/browser acceptance | [Web login](web-login.md) | Synthetic signed-fixture results separated from actual realm, TLS, roles and browser validation. |
| SETUP-PT / WT / PP / WP | Personal/Workspace testing/production authorization paths | [Setup guide](google-setup.md) | Selected path, project/API/audience/admin checks and authorization/read result; no secrets. |
| OPS-01 / OPS-02 | Container build/offline smoke | Commands below | Docker build/run exit status; no network or live calendar required for smoke. |
| OPS-03 | Offline worker integration in a read-only unprivileged container | [Worker commands](worker.md#unit-tests-and-offline-container-acceptance) | Real state/executor with fake providers: dry-run, create, restart/idempotency, health; no credentials/network. |
| DOC-01 / DOC-02 / DOC-03 | Documentation completeness and usability | Review checklist below | Reviewed sections, snippet checks and remaining manual setup gaps. |

Use [test-evidence.md](test-evidence.md). Allowed statuses: Passed, Failed, Not run, Not applicable. Give reasons for the last two and link follow-up work for failures. Explicitly note partial execution. A unit test simulating a revoked token does not establish a live revoked-token UAT result. One successful personal-account read does not prove all four setup paths.

## Container checks

Prerequisites: Docker daemon and dependency/image download access. OPS-01: run `docker build -t calendar-sync:test .`; expected exit 0. OPS-02: run `docker run --rm --network none --read-only calendar-sync:test`; expected exit 0 and the offline configuration-check message, without credentials, writes or network access. `--rm` removes the smoke container; remove the test image through your normal local image cleanup when no longer needed. CI performs these checks. No production Compose deployment or continuous sync is accepted by them.

## Documentation acceptance

DOC-01: verify every new/changed behavior maps to a catalogue case or an explicitly justified manual/Not applicable case. Run the catalogue checker; expected one-to-one discovered-test coverage. This catches missing/stale rows, not whether the prose or assertions are sufficient: review that manually.

DOC-02: follow the selected account/environment path in [google-setup.md](google-setup.md) and each relevant UAT prerequisite. Every command must name its working directory, inputs and expected result. Fixture instructions must specify actual event type, calendar assignment and recurrence settings. Read the entire shell block, including imports and delimiters. Do not perform live setup solely to review prose; mark live setup Not run when unavailable.

DOC-03: verify failure recovery and cleanup target only disposable test resources, production authorization is separated from current product readiness, sources support policy-sensitive statements, and the evidence record does not label unperformed live cases Passed. Expected result: owner can identify all remaining gaps before approving.

## Rules for future changes

Snapshot-helper focused checks: `python -m unittest discover -s tests -p 'test_state_snapshot.py' -v` expects 14 passes without credentials. The SNAP catalogue covers preserved pending intents, v1 compatibility, locks, permissions, partial verification and overwrite refusal. Backup/restore CLI writes only explicit new local files; no provider or secret-file contents are opened. Production backup/restore remains a separately recorded manual procedure.

Every issue must identify acceptance-case IDs or proposed new cases before implementation. Every PR must update the case catalogue/manual guide for changed behavior and map each acceptance criterion to its cases. Document prerequisites, inputs/fixtures, ordered actions or runnable commands, observable results including exit codes, negative/edge cases, state effects and cleanup. Mark unsupported cases explicitly rather than inventing expected behavior. Add a case when adding an automated test; preserve existing IDs and document all meaningful subcases.

The catalogue checker runs in CI. It prevents uncovered discovered tests or stale/duplicate catalogue entries, but does not measure semantic coverage, execute manual UAT, verify Google setup or enforce owner approval. No additional tests are needed solely to mirror low-impact prose edits; validate examples and catalogue instead. Changes still require owner-approved feature PRs; agents never merge.
