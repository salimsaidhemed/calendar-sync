# Automated test-case catalogue

Every discovered unit test has a stable case ID below. IDs refer to tests, not a claim that all possible failure modes are covered. New cases append new IDs; do not renumber existing IDs. Subcases in a test must be described in its input/expected columns. The source test is the executable detail; this catalogue describes what it proves.

## Shared procedure

Use the environment setup in [testing.md](testing.md). All 45 tests use temporary/fake state; none needs real credentials or provider requests. Run `python -m unittest discover -s tests -v` for the complete suite. To execute a row, use `python -m unittest discover -s tests -k '<full test identifier from the row>' -v`; expect exactly one test with `OK`. Prerequisites and cleanup are shared: Python 3.12+, installed project dependencies, local filesystem on Linux/macOS; temporary files are cleaned automatically. Expected exception assertions count as passing tests, not user-visible failures.

## CFG: test_scaffold.py

Source: [test_scaffold.py](../tests/test_scaffold.py). Run this group with `python -m unittest discover -s tests -p 'test_scaffold.py' -v`; expect 5 passing tests.

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| CFG-001 | `test_scaffold.SafetyTests.test_defaults_are_dry_run` | Empty environment | Dry-run is true. |
| CFG-002 | `test_scaffold.SafetyTests.test_ambiguous_boolean_rejected` | Empty/yes/1/misspelled boolean values | Each raises ValueError; none enables live mode. |
| CFG-003 | `test_scaffold.SafetyTests.test_insecure_or_secret_bearing_urls_rejected` | HTTP, credentials, query, fragment or missing host | Every unsafe URL is rejected. |
| CFG-004 | `test_scaffold.SafetyTests.test_live_mode_fails_closed` | SYNC_DRY_RUN=false | CLI exits 2 and refuses live sync. |
| CFG-005 | `test_scaffold.SafetyTests.test_scaffold_reports_no_sync` | Default CLI environment | CLI exits 0 and explicitly reports sync not implemented. |

## STATE: test_state.py

Source: [test_state.py](../tests/test_state.py). Run this group with `python -m unittest discover -s tests -p 'test_state.py' -v`; expect 13 passing tests.

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| STATE-001 | `test_state.StateTests.test_migration_reopen_and_permissions` | Create configuration, live mapping, successful run; reopen file | Schema v1, foreign keys, mapping/audit persist and file is 0600. |
| STATE-002 | `test_state.StateTests.test_future_schema_rejected_without_changes_and_lock_released` | Database user_version=999 | Open fails without downgrade; lock is released so repaired version can reopen. |
| STATE-003 | `test_state.StateTests.test_failed_migration_rolls_back_schema_and_version` | Inject invalid SQL into initial migration | No tables/version commit; subsequent valid open succeeds. |
| STATE-004 | `test_state.StateTests.test_second_process_cannot_open_state_and_release_allows_reopen` | Parent process holds store; competing store and subprocess | Both reject contention; subprocess succeeds after release. |
| STATE-005 | `test_state.StateTests.test_incremental_migration_preserves_existing_data` | Append temporary v2 migration to populated v1 store | Upgrade retains configuration; older app rejects v2. |
| STATE-006 | `test_state.StateTests.test_foreign_keys_reject_missing_configuration_and_cross_scope_run` | Missing configuration and another configuration's run ID | Repository/foreign keys reject references. |
| STATE-007 | `test_state.StateTests.test_configuration_isolation_and_paginated_audit` | Two configurations reuse event identities; paged audit query | Scoped mapping/audit reads isolate records; invalid page limit is rejected. |
| STATE-008 | `test_state.StateTests.test_destination_uniqueness_and_stable_identity` | Reuse href or UID for a second source; retarget existing source | Constraint/identity failures preserve one mapping and one audit entry. |
| STATE-009 | `test_state.StateTests.test_atomic_mapping_and_audit_rollback` | Inject audit failure after attempting fingerprint update | Mapping update rolls back; previous audit count/fingerprint remain. |
| STATE-010 | `test_state.StateTests.test_dry_run_cannot_create_or_advance_mapping` | Dry-run create/update; direct SQL ties mapping to dry-run run | No applied mapping/fingerprint change; SQL trigger also rejects. |
| STATE-011 | `test_state.StateTests.test_audit_is_append_only` | Attempt UPDATE and DELETE on existing audit row | Both abort; audit count stays one. |
| STATE-012 | `test_state.StateTests.test_run_completion_prevents_further_writes` | Finish interrupted run; attempt mapping/audit/second finish | All later writes through repository are rejected. |
| STATE-013 | `test_state.StateTests.test_configuration_rollback_and_transaction_contract` | Relative credential path; nested transaction after owner change | Invalid configuration rejected; nested transaction rolls owner change back. |

## OAUTH: test_google_oauth.py

Source: [test_google_oauth.py](../tests/test_google_oauth.py). Run this group with `python -m unittest discover -s tests -p 'test_google_oauth.py' -v`; expect 14 passing tests.

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| OAUTH-001 | `test_google_oauth.OAuthTests.test_local_authorization_uses_pkce_loopback_and_does_not_print_secrets` | Fake browser flow returns valid credentials | PKCE/offline consent, loopback host/ephemeral port, no printed URL/secrets, 0600 token and CLI success. |
| OAUTH-002 | `test_google_oauth.OAuthTests.test_valid_token_does_not_refresh` | Valid private token file | Access token returned; refresh is not called. |
| OAUTH-003 | `test_google_oauth.OAuthTests.test_expired_token_refresh_is_persisted` | Expired credentials; fake refresh rotates access/refresh token | Bounded request settings; rotated tokens persist atomically. |
| OAUTH-004 | `test_google_oauth.OAuthTests.test_revoked_refresh_keeps_old_file_and_sanitizes_exception_and_logs` | Refresh logs sentinel secret then raises invalid_grant | Fixed error only; old file unchanged; secret logs suppressed. |
| OAUTH-005 | `test_google_oauth.OAuthTests.test_missing_authorization_never_starts_browser_flow` | No token file | google_authorization_required; browser flow never constructed. |
| OAUTH-006 | `test_google_oauth.OAuthTests.test_rejects_broad_scope_and_untrusted_token_endpoint` | Stored write scope or non-Google token URI | Rejected before refresh. |
| OAUTH-007 | `test_google_oauth.OAuthTests.test_secret_file_permissions_symlinks_and_token_directory` | 0644 token, symlink token or 0755 parent directory | Each is rejected without using the secret. |
| OAUTH-008 | `test_google_oauth.OAuthTests.test_failed_replace_preserves_old_token_and_cleans_temporary_file` | Inject os.replace failure | Old token intact; no temporary secret files remain. |
| OAUTH-009 | `test_google_oauth.OAuthTests.test_token_lock_contention_fails_closed` | Hold token lock while attempting access | Contending access fails; access succeeds after release. |
| OAUTH-010 | `test_google_oauth.OAuthTests.test_authorization_failure_preserves_tokens_and_cli_redacts` | Browser-flow construction raises sentinel-bearing error | CLI exits 2, no secret output and old token remains. |
| OAUTH-011 | `test_google_oauth.OAuthTests.test_untrusted_client_endpoint_rejected_before_flow` | Desktop config with non-Google token URI | Rejected before creating authorization flow. |
| OAUTH-012 | `test_google_oauth.OAuthTests.test_missing_offline_grant_or_extra_granted_scope_keeps_old_token` | Consent lacks refresh token or grants an extra write scope | Authorization fails; existing token unchanged. |
| OAUTH-013 | `test_google_oauth.OAuthTests.test_client_file_cannot_be_replaced_by_token_file` | Same file supplied as client and token destination | Fixed distinct-files error; client is not overwritten. |
| OAUTH-014 | `test_google_oauth.OAuthTests.test_logging_controls_restored_after_error` | Exception inside sensitive diagnostics context | Original logging-disable setting restored. |

## GCAL: test_google_calendar.py

Source: [test_google_calendar.py](../tests/test_google_calendar.py). Run this group with `python -m unittest discover -s tests -p 'test_google_calendar.py' -v`; expect 13 passing tests.

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| GCAL-001 | `test_google_calendar.CalendarReadTests.test_reads_all_pages_including_empty_pages_and_preserves_markers` | Recurring master, empty intermediate page, cancelled record | All pages returned; IDs encoded; recurrence/cancellation retained; redirects/proxy inheritance disabled. |
| GCAL-002 | `test_google_calendar.CalendarReadTests.test_partial_pagination_failure_never_returns_partial_events` | First page succeeds; second returns 404 | Entire read fails with not-found code, no partial result. |
| GCAL-003 | `test_google_calendar.CalendarReadTests.test_repeated_page_token_and_duplicate_event_fail_closed` | Looped nextPageToken or duplicate source ID across pages | Entire read rejected. |
| GCAL-004 | `test_google_calendar.CalendarReadTests.test_rate_limit_retry_respects_retry_after` | 429 with Retry-After 7 then success | Wait seven seconds via fake sleeper; retry succeeds. |
| GCAL-005 | `test_google_calendar.CalendarReadTests.test_403_rate_limits_retry_but_permission_denial_does_not` | Rate-limit reason then success; separately forbidden reason | Rate limit retries with jitter; permission denial gets one attempt. |
| GCAL-006 | `test_google_calendar.CalendarReadTests.test_transient_network_errors_have_bounded_retries` | Repeated timeout containing sentinel text | Four attempts, three sleeps; safe exhausted error without sentinel. |
| GCAL-007 | `test_google_calendar.CalendarReadTests.test_permanent_failures_have_safe_specific_codes_without_retries` | 400/401/403/404/302 and unclassified 418 responses | Correct fixed status code; one attempt; response secrets omitted. |
| GCAL-008 | `test_google_calendar.CalendarReadTests.test_api_disabled_and_insufficient_scope_reasons_are_allowlisted` | Legacy/modern reason payloads; unknown secret-bearing reason | Disabled/scope reasons map to fixed codes; unknown maps to generic permission denial. |
| GCAL-009 | `test_google_calendar.CalendarReadTests.test_malformed_403_payload_keeps_safe_status_diagnostic` | Malformed JSON/shape, missing lists, invalid reason types | Safe permission-denied code without leaking payload. |
| GCAL-010 | `test_google_calendar.CalendarReadTests.test_redirects_auth_errors_and_tls_failures_are_not_retried` | 301/302/401 and TLS exception | No redirect/auth retry; TLS failure maps to safe code. |
| GCAL-011 | `test_google_calendar.CalendarReadTests.test_large_retry_after_defers_without_retrying_early` | 503 with Retry-After 3600 | Deferred error; no premature sleep/retry. |
| GCAL-012 | `test_google_calendar.CalendarReadTests.test_invalid_payloads_and_limits_fail_closed` | Invalid page/items/ID/token shape; event/page limit reached | Entire read fails; no truncated success. |
| GCAL-013 | `test_google_calendar.CalendarReadTests.test_library_debug_messages_and_payloads_are_suppressed` | HTTP library logs and raises sentinel token/event data | No secret diagnostics; fixed exception only. |

## Manual and environment cases

Manual Google cases and their runnable steps are in [google-acceptance.md](google-acceptance.md); four account/environment setup cases are in [google-setup.md](google-setup.md). Docker smoke and documentation review cases are in [testing.md](testing.md). Results belong in a dated [execution record](test-evidence.md), not as permanent PASS labels in this catalogue.

## Explicit coverage limits

Fake OAuth tests do not establish that a real tenant permits consent, Google has verified an app, all account classes work, or production is ready. Fake pagination does not establish a point-in-time Google snapshot. Windows/network filesystem behavior, real token expiry under each tenant, Nextcloud interoperability, provider writes, conflict resolution and full sync recovery have not been accepted here. Add documented cases with those features; record current unavailable scenarios as Not run or Not applicable with a reason, never silently passed.
