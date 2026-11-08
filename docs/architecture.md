# v0.1.0 implementation plan

This document specifies the target engine architecture. SQLite state, provider libraries, planner and opt-in conditional execution/recovery are implemented; continuous worker wiring remains future work. See [execution.md](execution.md) and the catalogue for verified behavior.

## Direction and ownership

Start with one Google Workspace master calendar feeding one explicitly selected Nextcloud CalDAV calendar at nextcloud.tcadc.com. Google is authoritative for supported fields. Never discover a destination by display name alone. Never infer matching events from titles or dates. Existing destination events are unowned and must remain untouched.

## Boundaries

Separate provider adapters, a pure planning layer, an execution layer, configuration repositories, and SQLite persistence. The worker should consume a configuration ID rather than global user state. A later FastAPI application can manage those configurations; Keycloak subject IDs identify owners, with authorization enforced per configuration. Store credential references separately from event mappings; never put refresh tokens in audit records.

## Persistence and idempotency acceptance criteria

- Versioned SQLite migrations; foreign keys, transactions and a process lock for one worker per state database.
- Sync configurations, mappings, run records and append-only audit entries scoped by configuration ID. Define audit retention before production use.
- Unique source identity and destination identity per configuration. Persist source fingerprints and destination ETags to detect changes.
- Deterministic destination UID and URL derived from configuration and source IDs; create with `If-None-Match: *`, update with `If-Match`. A timed-out create must be reconciled at its deterministic URL before retrying.
- Mark and verify owned events before mutation, including after restoring or losing the database. Never adopt a colliding destination merely because its UID matches.
- Dry-run may record plans/audit history but must never advance applied fingerprints or create mappings that claim a successful write.
- Never delete in v0.1.0. Source disappearance or cancellation produces a review/audit item. Empty or partial provider responses cannot imply deletion.
- Destination edits since last successful sync are conflicts: skip and audit. No automatic timestamp winner. Revalidate the ETag immediately before writing.
- Complete Google pagination before planning. Retry transient errors with bounded backoff and jitter; respect rate limits. Isolate failed events, retain previous successful state, and report an unhealthy run.

## OAuth and deployment acceptance criteria

Use an installed Google OAuth application with an explicit interactive loopback authorization command outside the headless worker. Request offline access and read-only calendar scope for this one-way design. Store client configuration and refresh tokens outside Git and image layers in permission-restricted files; use atomic token replacement and reject missing authorization without falling back to embedded credentials. Use a Nextcloud app password for the selected account. Never log event bodies, authorization headers or secret-bearing URLs. Support secret-file references before adding environment-based secret values.

Provide Docker volume backup/restore instructions, continuous polling with graceful signal handling, and a health signal based on the last completed run. SQLite backups must use the backup API or stop the worker to include WAL state. Do not run multiple replicas on the same database.

## Explicit event limitations for the first engine

Support ordinary single events only initially, including all-day exclusive end dates and timezone-aware timed events. Skip and audit recurring masters, recurring instances, recurrence exceptions and cancelled events. Do not flatten recurrence silently. Do not sync attendees, invitations, organizer identity, conferencing, attachments, reminders, or Google Chat. No bidirectional edits or automatic conflict resolution. Calendar permissions, ACLs and account provisioning are outside scope.

## Test gates before declaring v0.1.0 complete

Fake-provider tests cover repeated runs, crash after remote create/update before local commit, create collisions, ETag conflicts, incomplete pagination, provider outages, deleted source events, recurring events, DST/all-day dates, dry-run followed by live run, configuration isolation, token refresh and redaction. Current coverage is enumerated in [test-cases.md](test-cases.md). Live acceptance and worker/deployment gates remain separate requirements.
