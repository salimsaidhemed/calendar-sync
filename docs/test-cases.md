# Automated test-case catalogue

Every discovered unit test has a stable case ID below. IDs refer to tests, not a claim that all possible failure modes are covered. New cases append new IDs; do not renumber existing IDs. Subcases in a test must be described in its input/expected columns. The source test is the executable detail; this catalogue describes what it proves.

## Shared procedure

Use the environment setup in [testing.md](testing.md). All 78 tests use temporary/fake state; none needs real credentials or provider requests. Run `python -m unittest discover -s tests -v` for the complete suite. To execute a row, use `python -m unittest discover -s tests -k '<full test identifier from the row>' -v`; expect exactly one test with `OK`. Prerequisites and cleanup are shared: Python 3.12+, installed project dependencies, local filesystem on Linux/macOS; temporary files are cleaned automatically. Expected exception assertions count as passing tests, not user-visible failures.

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

## NORM: test_events.py

Source: [test_events.py](../tests/test_events.py). Run `python -m unittest discover -s tests -p 'test_events.py' -v`; expect 13 tests with `OK`. All fixtures are local/fake; no credentials or provider requests.

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| NORM-001 | `test_events.EventTests.test_timed_events_canonicalize_to_utc_without_mutating_source` | Offset-aware Google event with Madrid zone | UTC start is correct; source dictionary unchanged. |
| NORM-002 | `test_events.EventTests.test_all_day_dates_preserve_exclusive_end_across_dst` | One-day all-day event on spring DST date | Serialized DTSTART/DTEND round-trip as dates with exclusive next-day end. |
| NORM-003 | `test_events.EventTests.test_offset_dst_transition_has_correct_elapsed_time` | Spring-forward offset change across 01:30–03:30 | Elapsed UTC duration is one hour. |
| NORM-004 | `test_events.EventTests.test_named_zone_without_offset_accepts_only_unambiguous_times` | Normal named-zone local time; DST gap and fold | Normal time supported; nonexistent/ambiguous local time skipped. |
| NORM-005 | `test_events.EventTests.test_floating_offset_mismatch_unknown_zone_and_subseconds_are_skipped` | No zone, mismatched offset, unknown zone or nonzero fractional second | Fixed floating/mismatch/invalid/subsecond reasons; no event. |
| NORM-006 | `test_events.EventTests.test_recurrence_masters_instances_exceptions_and_cancelled_are_skipped` | Recurrence/recurringEventId/originalStartTime markers; cancelled event | Recurrence or cancellation reason; no flattened event. |
| NORM-007 | `test_events.EventTests.test_unsupported_types_and_fields_have_safe_reason_codes` | Birthday type; ordinary event with attendees/organizer/reminders | Unsupported type skipped; ordinary supported fields retained with named omissions and no invitation fields/secrets in serialization. |
| NORM-008 | `test_events.EventTests.test_invalid_dates_mixed_types_missing_end_and_nonpositive_duration` | Invalid date, mixed boundary types, missing end or zero duration | No normalized event accepted. |
| NORM-009 | `test_events.EventTests.test_invalid_ids_and_text_are_rejected_without_leaking_values` | Empty/whitespace source ID, NUL text or non-dictionary | Fixed rejection reason without sentinel text. |
| NORM-010 | `test_events.EventTests.test_serialization_roundtrips_unicode_escaping_and_line_folding` | Long Unicode summary; delimiter/newline/BEGIN text in description | One VEVENT only, text/date values preserved, folded lines at most 75 bytes. |
| NORM-011 | `test_events.EventTests.test_fingerprints_ignore_key_order_and_omitted_fields_but_track_supported_changes` | Reordered input, attendee-only change, then summary change | First fingerprints match; supported-field change differs. |
| NORM-012 | `test_events.EventTests.test_serialization_requires_safe_uid_and_aware_stamp_and_model` | Empty/control/long UID; naive stamp, wrong model or floating boundaries | Serialization/model construction rejected. |
| NORM-013 | `test_events.EventTests.test_repr_does_not_expose_event_content` | Sentinel event summary in result/model | Neither repr contains event data. |

## DAV: test_caldav.py

Source: [test_caldav.py](../tests/test_caldav.py). Run `python -m unittest discover -s tests -p 'test_caldav.py' -v`; expect 20 tests with `OK`. All fixtures are local/fake; no credentials or provider requests.

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| DAV-001 | `test_caldav.CalDAVTests.test_report_uses_explicit_calendar_verified_tls_and_no_writes` | One fake 207 resource with UID and strong ETag | One REPORT to exact URL; TLS/timeouts set, no redirects/env proxy, UID/ETag/href retained. |
| DAV-002 | `test_caldav.CalDAVTests.test_constructor_rejects_insecure_foreign_and_ambiguous_urls_before_network` | HTTP, foreign origin, query, fragment, credentials, traversal/encoded separators, missing slash | Configuration rejected before network. |
| DAV-003 | `test_caldav.CalDAVTests.test_href_scope_rejects_other_origins_siblings_nested_and_traversal` | Malicious/foreign/out-of-calendar resource hrefs | Whole report rejected; no href is followed. |
| DAV-004 | `test_caldav.CalDAVTests.test_same_origin_absolute_and_encoded_hrefs_are_canonicalized` | Same-origin absolute percent-encoded resource path | Canonical in-calendar href retained. |
| DAV-005 | `test_caldav.CalDAVTests.test_bad_secret_permissions_missing_symlink_and_empty_password_fail_before_network` | 0644 app password, symlink, empty file or missing file | Safe failure before request. |
| DAV-006 | `test_caldav.CalDAVTests.test_redirect_auth_permission_not_found_and_partial_status_codes_are_safe` | 302/401/403/404/507/500 response with sentinel body | Fixed redirect/auth/access/missing/partial/read code, no raw payload. |
| DAV-007 | `test_caldav.CalDAVTests.test_tls_and_interrupted_transport_fail_without_partial_results` | TLS exception or interrupted streamed response | Safe failure, never partial successful resources. |
| DAV-008 | `test_caldav.CalDAVTests.test_dtd_entities_malformed_xml_and_unexpected_roots_are_rejected` | External entity/DTD, malformed XML, HTML, DAV error or unknown limit node | Safe rejection without entity expansion. |
| DAV-009 | `test_caldav.CalDAVTests.test_missing_properties_and_failed_propstat_reject_entire_report` | Valid first resource followed by missing data/ETag or failed member status | Entire report fails. |
| DAV-010 | `test_caldav.CalDAVTests.test_duplicate_href_uid_and_weak_etag_fail_closed` | Repeated href, repeated UID across resources or weak ETag | No ambiguous/colliding resource list returned. |
| DAV-011 | `test_caldav.CalDAVTests.test_response_and_resource_limits_do_not_return_truncated_success` | Exceed byte bound or resource count | Explicit limit failure, not truncated success. |
| DAV-012 | `test_caldav.CalDAVTests.test_recurrence_cancelled_alarm_and_floating_resources_have_skip_reasons` | RRULE/RDATE/EXDATE/RECURRENCE-ID, CANCELLED, VALARM; floating/missing/mixed/nonpositive event times | Raw resource retained with recurrence/cancelled/component/time skip code. |
| DAV-013 | `test_caldav.CalDAVTests.test_invalid_calendar_data_or_missing_uid_is_rejected` | Invalid iCalendar, missing UID or duplicate UID property | Entire read rejected. |
| DAV-014 | `test_caldav.CalDAVTests.test_empty_calendar_is_a_read_result_not_delete_instruction` | Empty valid multistatus | Empty resource list; no delete API. |
| DAV-015 | `test_caldav.CalDAVTests.test_credentials_event_data_and_raw_exceptions_are_not_logged` | HTTP client logs/raises sentinel password and event data | Logs suppressed; exception contains no sentinel. |

## DAV loopback HTTP test cases

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| DAV-016 | `test_caldav.CalDAVTests.test_http_loopback_is_rejected_without_explicit_opt_in` | HTTP localhost, IPv4/IPv6 loopback without flag | Reject before network with HTTPS-required code. |
| DAV-017 | `test_caldav.CalDAVTests.test_explicit_http_loopback_reads_preserve_scheme_and_exact_origin` | Explicit HTTP flag and matching localhost/127.0.0.1/::1 origin | HTTP REPORT and resource href preserved; redirects still refused. |
| DAV-018 | `test_caldav.CalDAVTests.test_http_opt_in_rejects_non_loopback_hosts_before_network` | HTTP remote/LAN, wildcard, lookalike, shorthand/mapped-IP hosts with flag | Loopback-only error before any request. |
| DAV-019 | `test_caldav.CalDAVTests.test_http_opt_in_requires_boolean_and_matching_explicit_origin` | String/integer/None flag; default HTTPS or wrong-port origin | Strict boolean/origin errors; no request. |
| DAV-020 | `test_caldav.CalDAVTests.test_http_loopback_still_rejects_resource_escapes_and_redirects` | Local HTTP report with foreign host/port/path/traversal href or 302 | Whole read rejected; no redirect or resource href followed. |

## Manual and environment cases

Manual Google cases are in [google-acceptance.md](google-acceptance.md); Nextcloud read/normalization cases are in [nextcloud.md](nextcloud.md). Four Google account/environment setup cases are in [google-setup.md](google-setup.md). Docker smoke and documentation review cases are in [testing.md](testing.md). Results belong in a dated [execution record](test-evidence.md), not as permanent PASS labels in this catalogue.

## Explicit coverage limits

Fake OAuth tests do not establish that a real tenant permits consent, Google has verified an app, all account classes work, or production is ready. Fake pagination does not establish a point-in-time Google snapshot. Windows/network filesystem behavior, real token expiry under each tenant, Nextcloud interoperability, provider writes, conflict resolution and full sync recovery have not been accepted here. Add documented cases with those features; record current unavailable scenarios as Not run or Not applicable with a reason, never silently passed.
