# Automated test-case catalogue

Every discovered unit test has a stable case ID below. IDs refer to tests, not a claim that all possible failure modes are covered. New cases append new IDs; do not renumber existing IDs. Subcases in a test must be described in its input/expected columns. The source test is the executable detail; this catalogue describes what it proves.

## Shared procedure

Use the environment setup in [testing.md](testing.md). All 141 tests use temporary/fake state; none needs real credentials or provider requests. Run `python -m unittest discover -s tests -v` for the complete suite. To execute a row, use `python -m unittest discover -s tests -k '<full test identifier from the row>' -v`; expect exactly one test with `OK`. Prerequisites and cleanup are shared: Python 3.12+, installed project dependencies, local filesystem on Linux/macOS; temporary files are cleaned automatically. Expected exception assertions count as passing tests, not user-visible failures.

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
| STATE-001 | `test_state.StateTests.test_migration_reopen_and_permissions` | Create configuration, live mapping, successful run; reopen file | Current schema, foreign keys, mapping/audit persist and file is 0600. |
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

Source: [test_caldav.py](../tests/test_caldav.py). Run `python -m unittest discover -s tests -p 'test_caldav.py' -v`; expect 29 tests with `OK`. All fixtures are local/fake; no credentials or provider requests.

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

## DAV read diagnostic cases

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| DAV-021 | `test_caldav.CalDAVTests.test_connection_timeout_and_stream_errors_have_safe_distinct_codes` | Connection refusal/DNS, connect/read timeout, chunked/decompression errors carrying sentinel secrets | Fixed connection_failed/timeout/response_interrupted codes; no secret text. |
| DAV-022 | `test_caldav.CalDAVTests.test_xml_syntax_and_forbidden_entities_have_safe_distinct_codes` | Malformed XML or forbidden external entity/DTD with sentinel path | Fixed xml_invalid/xml_rejected codes; no payload/path leakage. |
| DAV-023 | `test_caldav.CalDAVTests.test_icalendar_parser_exceptions_are_sanitized_as_calendar_data_errors` | iCalendar parser raises sentinel-bearing ValueError | Fixed invalid_calendar_data code, no event content. |


## PLAN: test_planner.py

Source: [test_planner.py](../tests/test_planner.py). Run `python -m unittest discover -s tests -p 'test_planner.py' -v`; expect 29 tests with `OK`. Inputs use fake provider responses, owned payloads and temporary SQLite state; no real credentials or event writes.

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| PLAN-001 | `test_planner.PlannerTests.test_new_source_plans_create_with_stable_identity_and_no_input_mutation` | Same new source planned twice | Equal dry-run create plans and deterministic href/UID; source unchanged and no execute API. |
| PLAN-002 | `test_planner.PlannerTests.test_identity_is_configuration_calendar_and_source_scoped` | Change configuration, source/destination calendar, source ID or title | Identity changes for scope/ID, not for title edits. |
| PLAN-003 | `test_planner.PlannerTests.test_incomplete_snapshots_never_produce_partial_plans` | False or nonboolean completeness assertions | Planning fails before returning any plan. |
| PLAN-004 | `test_planner.PlannerTests.test_matching_mapping_markers_content_and_etag_plan_unchanged` | Matching saved mapping and marked baseline resource | One unchanged action with expected ETag. |
| PLAN-005 | `test_planner.PlannerTests.test_changed_source_plans_update_only_after_owned_baseline_checks` | Source summary changed; destination baseline intact | Update proposal with new fingerprint and saved ETag. |
| PLAN-006 | `test_planner.PlannerTests.test_changed_destination_etag_conflicts_even_when_source_is_unchanged` | Destination ETag changed | Conflict regardless of unchanged source. |
| PLAN-007 | `test_planner.PlannerTests.test_unmapped_existing_resource_is_never_adopted_even_with_valid_markers` | Existing identity with markers but missing mapping; also unmarked payload | Conflict, never adoption/recreation. |
| PLAN-008 | `test_planner.PlannerTests.test_href_and_uid_collisions_never_plan_create` | Unmapped href/UID collision or target reserved by another mapping | No create; conflict reported. |
| PLAN-009 | `test_planner.PlannerTests.test_matching_titles_dates_are_not_used_to_adopt_unowned_events` | Same title/date content at unrelated UID/href | Unowned event is untouched; new source gets its own deterministic create proposal. |
| PLAN-010 | `test_planner.PlannerTests.test_missing_destination_or_changed_mapping_identity_conflicts` | Missing destination, altered mapping UID or replaced resource UID | Fixed destination/mapping identity conflict. |
| PLAN-011 | `test_planner.PlannerTests.test_missing_cancelled_and_recurring_sources_never_generate_delete` | Mapped source absent/cancelled/recurring; unmapped cancellation | Missing or unsupported action; no delete outcome. |
| PLAN-012 | `test_planner.PlannerTests.test_unsupported_destination_is_never_overwritten` | Mapped destination marked recurring/unsupported | Conflict rather than update. |
| PLAN-013 | `test_planner.PlannerTests.test_ownership_markers_and_real_content_must_match_saved_baseline` | Alter each marker or actual summary; add scheduling METHOD or wrong calendar VERSION | Ownership/baseline conflict even if ETag is unchanged. |
| PLAN-014 | `test_planner.PlannerTests.test_extra_unsupported_properties_or_invalid_owned_payload_conflict` | Add attendee or corrupt raw owned data | Conflict; unsupported content not silently overwritten. |
| PLAN-015 | `test_planner.PlannerTests.test_duplicate_sources_destinations_and_mappings_fail_closed` | Duplicate source ID, resource or mapping rows | Whole planning call fails. |
| PLAN-016 | `test_planner.PlannerTests.test_out_of_scope_resources_and_mappings_are_rejected` | Foreign resource/mapping URL, wrong config, weak ETag or invalid baseline hash | Whole planning call fails with fixed code. |
| PLAN-017 | `test_planner.PlannerTests.test_plan_order_is_deterministic_and_omissions_are_reported` | Reverse input order; include sentinel attendee | Equal ordered plans; omitted field names retained; repr excludes private values. |
| PLAN-018 | `test_planner.PlannerTests.test_invalid_sources_and_contexts_produce_safe_failures` | Malformed source, invalid context/primary alias, live-plan flag or delete action | Unsupported source or fixed validation error; dry-run cannot be disabled. |
| PLAN-019 | `test_planner.PlannerTests.test_all_day_owned_payload_and_marker_hashes_roundtrip` | Serialize/plan mapped all-day baseline | Unchanged action, hashed markers correct and no raw source calendar ID in payload. |
| PLAN-020 | `test_planner.PreviewTests.test_preview_repeated_runs_do_not_create_runs_audits_or_mappings` | Repeated preview with fake readers and temporary SQLite | Stable create proposal; no run/audit/mapping rows added. |
| PLAN-021 | `test_planner.PreviewTests.test_preview_reads_only_configuration_scoped_applied_mappings_without_advancing_them` | Current and foreign configurations have applied rows; source changes | Only selected configuration loaded; update proposed; mappings/audit history unchanged. |
| PLAN-022 | `test_planner.PreviewTests.test_unknown_configuration_or_provider_scope_mismatch_stops_before_reads` | Unknown configuration or mismatched Google/CalDAV calendar | Fail before provider read calls. |
| PLAN-023 | `test_planner.PreviewTests.test_provider_failure_never_creates_partial_plan_or_applied_state` | Destination/source reader raises; source failure must stop destination read | Fixed failure with no plan/mapping/audit writes; no second reader on source failure. |

## DAV: test_caldav.py conditional transport

Run `python -m unittest discover -s tests -p 'test_caldav.py' -v`; expect 29 passing tests. Inputs use local fakes and temporary state with automatic cleanup; no credentials or provider access. Source: [test_caldav.py](../tests/test_caldav.py).

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| DAV-024 | `test_caldav.CalDAVTests.test_targeted_get_checks_etag_scope_and_only_404_means_absent` | targeted get checks etag scope and only 404 means absent (fake HTTP) | Scoped GET returns strong-ETag resource; only 404 means absent; auth/redirect/foreign href fail safely. |
| DAV-025 | `test_caldav.CalDAVTests.test_targeted_get_rejects_missing_weak_etag_and_oversized_data` | targeted get rejects missing weak etag and oversized data (fake HTTP) | GET missing/weak ETag or oversized body fails closed. |
| DAV-026 | `test_caldav.CalDAVTests.test_put_is_disabled_by_default_and_requires_one_valid_precondition` | put is disabled by default and requires one valid precondition (fake HTTP) | PUT without explicit opt-in or valid create/update precondition sends no request; weak/wildcard/injected ETags rejected. |
| DAV-027 | `test_caldav.CalDAVTests.test_conditional_create_and_update_put_headers_are_exact` | conditional create and update put headers are exact (fake HTTP) | Create/update send exactly If-None-Match:* or strong If-Match; TLS, private auth, timeouts, redirect/proxy refusal preserved. |
| DAV-028 | `test_caldav.CalDAVTests.test_put_conflicts_redirects_and_transport_failures_are_sanitized` | put conflicts redirects and transport failures are sanitized (fake HTTP) | 409/412 return conflict subtype; redirects/auth/permission/transport errors sanitized without sentinel payload. |
| DAV-029 | `test_caldav.CalDAVTests.test_put_refuses_foreign_resource_empty_or_oversized_payload_before_network` | put refuses foreign resource empty or oversized payload before network (fake HTTP) | Foreign URL, empty/nonbytes or oversized payload rejected before network. |

## EXEC: test_executor.py

Run `python -m unittest discover -s tests -p 'test_executor.py' -v`; expect 25 passing tests. Inputs use local fakes and temporary state with automatic cleanup; no credentials or provider access. Source: [test_executor.py](../tests/test_executor.py).

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| EXEC-001 | `test_executor.ExecutorTests.test_default_dry_run_has_no_put_intent_run_or_mapping` | default dry run has no put intent run or mapping (fake providers/SQLite) | Default preview proposes create; zero PUT, intents, runs, audit and mappings. |
| EXEC-002 | `test_executor.ExecutorTests.test_dry_run_then_live_then_repeat_creates_one_resource_and_mapping` | dry run then live then repeat creates one resource and mapping (fake providers/SQLite) | Dry-run then live then repeat yields one PUT/resource/mapping; repeated live unchanged. |
| EXEC-003 | `test_executor.ExecutorTests.test_source_edit_updates_same_identity_using_saved_etag` | source edit updates same identity using saved etag (fake providers/SQLite) | Source summary edit conditionally updates the same URL/UID using saved ETag and confirmed fingerprint. |
| EXEC-004 | `test_executor.ExecutorTests.test_destination_edit_after_plan_is_detected_before_put` | destination edit after plan is detected before put (fake providers/SQLite) | Destination ETag changes during targeted GET; conflict before PUT, saved mapping preserved. |
| EXEC-005 | `test_executor.ExecutorTests.test_412_race_after_fresh_get_never_overwrites_user_edit` | 412 race after fresh get never overwrites user edit (fake providers/SQLite) | Destination changes between GET and PUT; 412 preserves remote edit and saved mapping. |
| EXEC-006 | `test_executor.ExecutorTests.test_unowned_collision_never_puts_or_adopts` | unowned collision never puts or adopts (fake providers/SQLite) | Unmapped existing resource with ownership markers conflicts; no adoption or PUT. |
| EXEC-007 | `test_executor.ExecutorTests.test_create_race_after_plan_preserves_foreign_resource` | create race after plan preserves foreign resource (fake providers/SQLite) | Foreign resource appears at create PUT; precondition preserves foreign body and no mapping. |
| EXEC-008 | `test_executor.ExecutorTests.test_timeout_after_remote_create_is_confirmed_without_second_put` | timeout after remote create is confirmed without second put (fake providers/SQLite) | PUT creates then times out; verified operation readback completes mapping with one PUT. |
| EXEC-009 | `test_executor.ExecutorTests.test_crash_after_remote_create_recovers_after_database_reopen_without_put` | crash after remote create recovers after database reopen without put (fake providers/SQLite) | Crash after remote create then reopen; pending journal recovers with one PUT/resource, prior run interrupted. |
| EXEC-010 | `test_executor.ExecutorTests.test_crash_after_remote_update_recovers_new_fingerprint_without_put` | crash after remote update recovers new fingerprint without put (fake providers/SQLite) | Crash after remote update then reopen; old mapping retained until confirmed recovery with no extra PUT. |
| EXEC-011 | `test_executor.ExecutorTests.test_failed_put_before_apply_retries_only_after_reconciliation` | failed put before apply retries only after reconciliation (fake providers/SQLite) | Uncertain unapplied PUT then rerun; reconcile absence before retrying same intent, one remote copy. |
| EXEC-012 | `test_executor.ExecutorTests.test_missing_success_etag_or_lost_readback_does_not_commit_mapping` | missing success etag or lost readback does not commit mapping (fake providers/SQLite) | Lost readback after successful PUT; no mapping until later verified readback, no second PUT. |
| EXEC-013 | `test_executor.ExecutorTests.test_user_edit_after_uncertain_create_blocks_recovery_without_overwrite` | user edit after uncertain create blocks recovery without overwrite (fake providers/SQLite) | User alters created event body before recovery; conflict even with retained markers, no overwrite/adoption. |
| EXEC-014 | `test_executor.ExecutorTests.test_pending_source_change_or_cancellation_is_not_retried` | pending source change or cancellation is not retried (fake providers/SQLite) | Pending unapplied intent then source cancellation; no retry or mapping, conflict recorded. |
| EXEC-015 | `test_executor.ExecutorTests.test_pending_retry_limit_is_durable_across_invocations` | pending retry limit is durable across invocations (fake providers/SQLite) | One-attempt budget and repeated failure; durable pending retry-limit with only one PUT. |
| EXEC-016 | `test_executor.ExecutorTests.test_dry_run_never_recovers_pending_intent_or_advances_mapping` | dry run never recovers pending intent or advances mapping (fake providers/SQLite) | Dry-run after interrupted write leaves journal and mappings untouched and performs no recovery PUT. |
| EXEC-017 | `test_executor.ExecutorTests.test_missing_cancelled_and_recurring_sources_never_delete_destination` | missing cancelled and recurring sources never delete destination (fake providers/SQLite) | Mapped source missing/cancelled/recurring; destination and mapping preserved, no delete or extra PUT. |
| EXEC-018 | `test_executor.ExecutorTests.test_duplicate_or_partial_preflight_stops_before_run_and_put` | duplicate or partial preflight stops before run and put (fake providers/SQLite) | Duplicate source IDs or source read failure stops before execution run/PUT. |
| EXEC-019 | `test_executor.ExecutorTests.test_atomic_finalize_failure_leaves_journal_for_recovery` | atomic finalize failure leaves journal for recovery (fake providers/SQLite) | Applied-audit failure rolls mapping/audit back; retained journal later recovers without duplicate PUT. |
| EXEC-020 | `test_executor.ExecutorTests.test_successful_intent_is_not_reapplied_after_run_finish_crash` | successful intent is not reapplied after run finish crash (fake providers/SQLite) | Crash after mapping completion before run finish; next invocation unchanged, no reapplication. |
| EXEC-021 | `test_executor.ExecutorTests.test_one_event_failure_does_not_prevent_other_source_success` | one event failure does not prevent other source success (fake providers/SQLite) | Two sources, first write fails; second applies, failed run reports one pending and one applied. |
| EXEC-022 | `test_executor.ExecutorTests.test_invalid_live_options_and_report_repr_do_not_expose_private_data` | invalid live options and report repr do not expose private data (fake providers/SQLite) | Invalid dry-run types/budgets rejected; report/item repr omit sentinel private event data. |
| EXEC-023 | `test_executor.ExecutorTests.test_payload_hash_mismatch_prevents_retry_after_serializer_change` | payload hash mismatch prevents retry after serializer change (fake providers/SQLite) | Pending intent payload reconstruction changes; hash mismatch blocks retry. |
| EXEC-024 | `test_executor.ExecutorTests.test_source_edit_or_cancellation_after_destination_get_blocks_put` | source edit or cancellation after destination get blocks put (fake providers/SQLite) | Edit or cancel source during destination GET; fresh complete source read blocks stale PUT. |
| EXEC-025 | `test_executor.ExecutorTests.test_source_revalidation_outage_or_duplicate_blocks_put_and_keeps_intent` | source revalidation outage or duplicate blocks put and keeps intent (fake providers/SQLite) | Fresh source read outage or duplicate IDs leaves pending intent, no PUT or mapping. |

## JOURNAL: test_journal.py

Run `python -m unittest discover -s tests -p 'test_journal.py' -v`; expect 6 passing tests. Inputs use local fakes and temporary state with automatic cleanup; no credentials or provider access. Source: [test_journal.py](../tests/test_journal.py).

| Case ID | Executable test identifier | Setup / input | Expected result |
| --- | --- | --- | --- |
| JOURNAL-001 | `test_journal.JournalTests.test_v1_upgrade_preserves_mapping_run_and_append_only_audit` | v1 upgrade preserves mapping run and append only audit (fake providers/SQLite) | Populate v1 with mappings/runs/audit; upgrade preserves all data and append-only protection, adds manual purpose. |
| JOURNAL-002 | `test_journal.JournalTests.test_intent_requires_active_live_execution_and_valid_hashes` | intent requires active live execution and valid hashes (fake providers/SQLite) | Dry/manual/completed runs and malformed hashes cannot create intents. |
| JOURNAL-003 | `test_journal.JournalTests.test_pending_uniqueness_identity_immutability_and_retention` | pending uniqueness identity immutability and retention (fake providers/SQLite) | Duplicate pending source, immutable identity edit and journal deletion rejected; attempt limit persists. |
| JOURNAL-004 | `test_journal.JournalTests.test_cross_configuration_audit_cannot_reference_foreign_intent` | cross configuration audit cannot reference foreign intent (fake providers/SQLite) | Foreign configuration cannot read, complete or audit-reference another configuration’s intent. |
| JOURNAL-005 | `test_journal.JournalTests.test_local_mapping_changed_blocks_atomic_finalization` | local mapping changed blocks atomic finalization (fake providers/SQLite) | Changed local mapping blocks atomic completion and retains pending intent without additional applied audit. |
| JOURNAL-006 | `test_journal.JournalTests.test_restart_interrupts_execution_only_and_is_configuration_scoped` | restart interrupts execution only and is configuration scoped (fake providers/SQLite) | Restart interrupts only selected configuration’s execution runs; manual/other runs untouched. |

## Manual and environment cases

Manual Google cases are in [google-acceptance.md](google-acceptance.md); Nextcloud cases in [nextcloud.md](nextcloud.md); planner cases in [planner.md](planner.md); execution cases EXEC-UAT-01–07 in [execution.md](execution.md). Four Google account/environment setup cases are in [google-setup.md](google-setup.md). Docker smoke and documentation review cases are in [testing.md](testing.md). Results belong in a dated [execution record](test-evidence.md), not as permanent PASS labels in this catalogue.

## Explicit coverage limits

Fake OAuth tests do not establish that a real tenant permits consent, Google has verified an app, all account classes work, or production is ready. Fake pagination does not establish a point-in-time Google snapshot. Conditional writes/recovery are verified against fakes only. Windows/network filesystem behavior, real token expiry under each tenant, Nextcloud write interoperability, conflict resolution and production worker recovery have not been accepted here. Record unavailable live scenarios as Not run or Not applicable with a reason, never silently passed.
