# Changelog

## Unreleased

### Changed

- Compose now explicitly starts the continuous worker, with mounted credential directories, persistent state, health checks, 120-second stop grace and restart unless stopped; the bare image/CLI remains an offline check.
- Main ruleset now requires the verified GitHub Actions test check and an up-to-date branch, preserving existing PR/deletion/force-push safeguards. Documented the current public visibility and unresolved shared-author/owner approval boundary (#8).
- Google/CalDAV reads support cooperative shutdown; provider Retry-After hints defer worker cycles. SQLite fixture connections now close explicitly to avoid Python 3.14 resource warnings.
- CalDAV failures now distinguish connection, timeout, interrupted stream, forbidden/malformed XML and iCalendar parsing using fixed, secret-free diagnostic codes.
- Added an explicit loopback-only HTTP test option for CalDAV, with strict boolean opt-in and matching origin; HTTPS remains mandatory by default and non-loopback HTTP is refused.
- Google UAT fixture checks exclude cancelled records while still requiring exactly three active test events; the adapter continues returning cancellation markers.
- Calendar read failures distinguish disabled API, insufficient scope, rejected token, calendar access and rejected redirects using fixed diagnostic codes without exposing provider payloads.

### Added

- Continuous worker/one-cycle and health CLI commands with strict configuration, dry-run defaults, lifetime state locking, private atomic health snapshots, jittered polling/backoff and graceful SIGTERM/SIGINT handling.
- Worker/signal/rate-limit/health tests, offline real-engine container smoke, Compose validation in CI and complete worker setup/acceptance cases.
- Opt-in conditional Nextcloud executor with complete preflight, ownership revalidation, create/update preconditions, confirmed readback and no delete path; library dry-run remains the default and the CLI stays offline.
- SQLite schema v2 durable write intents, attempt limits, operation-scoped audit reasons and atomic mapping/audit/journal completion; recovery reconciles ambiguous creates/updates before retrying.
- Documented fake-provider crash/race tests, populated v1 upgrade tests and disposable-calendar execution acceptance steps. Live acceptance remains separate from automated results.
- Pure configuration-scoped dry-run planner with deterministic identities, six proposal outcomes and strict ownership/baseline/ETag checks; no automatic adoption, deletes or provider writes.
- Read-only configuration/mapping snapshots and provider preview orchestration that do not create runs/audit rows or advance applied mappings.
- 23 documented planner/preview tests and complete offline/live-read acceptance instructions, including lost-state collisions, missing/cancelled sources and destination edits.
- Read-only Nextcloud CalDAV REPORT adapter with explicit HTTPS origin/calendar boundaries, private app-password files, safe XML parsing, strong ETags and bounded complete-response validation.
- Provider-neutral event normalization and iCalendar serialization with UTC timed events, exclusive all-day ends, DST checks, stable fingerprints and documented recurrence/field skip reasons.
- 33 documented CalDAV/normalization tests and complete offline/live Nextcloud acceptance instructions; no provider writes or automatic sync introduced.
- Personal and Workspace Google Calendar/API/OAuth setup paths for UAT and production authorization, including audience rules, administrator controls and lifecycle limits.
- Traceable catalogue for all 45 automated tests, individual manual UAT cases, exact event fixture controls and a review evidence template.
- CI test-catalogue completeness check and issue/PR rules mapping acceptance criteria to documented cases.
- Feature-specific unit-test and user-acceptance instructions for Google OAuth/reads, plus mandatory testing sections in every PR's template and contribution rules.
- Explicit local Google Desktop OAuth authorization with PKCE, loopback redirect and read-only event scope.
- Locked, permission-restricted token files with atomic refresh persistence and sanitized failure codes.
- Google Calendar adapter with complete pagination, bounded retries, duplicate/page-loop detection and fail-closed partial reads.
- Credential-free OAuth/read tests and Google Workspace authorization/setup guide.
- Configuration-scoped SQLite persistence with atomic versioned migrations, worker locking, unique event mappings and run history.
- Append-only audit entries committed atomically with applied mappings; dry-run runs cannot create or advance applied mappings.
- State recovery and integration guide with credential-file references and no stored credential contents.
- Python package scaffold with dry-run defaults and explicit refusal of live mode.
- Dockerfile, Compose example and credential-free CI tests.
- Architecture acceptance criteria for secure OAuth, SQLite mappings, duplicate prevention and conflict handling.
- Five delivery milestones, linked epic/task roadmap, contribution workflow and PR/issue templates.

Continuous synchronization is implemented; complete production operations/acceptance remain under #16/#14. No v0.1.0 release has been published.
