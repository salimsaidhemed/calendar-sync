# Google calendar access troubleshooting

Older checkouts return only `google_calendar_access_failed` for non-retryable responses. Update to the current merged code or the PR under review, reinstall with `python -m pip install -e .` if needed, and rerun the same read to obtain a fixed diagnostic code. None includes tokens, calendar IDs, URLs or raw Google error messages. For personal/Workspace test and production-authorization configuration, use [the setup guide](google-setup.md).

| Code | Interpretation and next step |
| --- | --- |
| `google_calendar_api_disabled` | Google returned HTTP 403 with a disabled-service reason. Enable Google Calendar API in the Cloud project belonging to the downloaded OAuth client, wait for activation to propagate, then retry. |
| `google_calendar_scope_insufficient` | Google reported insufficient OAuth scope. Reauthorize the test account using the current Desktop client/read-only event scope; read using the resulting token file. |
| `google_calendar_token_rejected` | HTTP 401: the API rejected the access token. Reauthorize explicitly if repeat reads still fail. |
| `google_calendar_permission_denied` | Other HTTP 403: check that the authorized account can view the calendar's event details and that Workspace app-access policies permit the client. This code alone does not establish the exact reason. |
| `google_calendar_not_found` | HTTP 404: the calendar ID may be wrong or the authorized account may lack access. Use Calendar ID from settings, not its display name, browser URL or iCal feed URL. |
| `google_calendar_request_invalid` | HTTP 400: check the exact calendar ID and request configuration; report the fixed code if a valid fixture still fails. |
| `google_calendar_redirect_rejected` | An HTTP redirect was refused without forwarding credentials. Check the network environment; do not disable redirect/TLS safeguards. |
| `google_calendar_access_failed` | An unclassified non-retryable status. Report the fixed code without raw responses or secrets. |

API-disabled and insufficient-scope reasons are recognized from `error.errors` or `error.details` using a small fixed allowlist. Unknown or malformed reasons retain the safe HTTP-status diagnostic. Rate-limit retries, rejection of partial reads and dry-run defaults remain unchanged. See [Google's error reference](https://developers.google.com/workspace/calendar/api/guides/errors) for status semantics.

## Unit tests

With the environment from [the acceptance guide](google-acceptance.md) activated, run:

```sh
python -m unittest discover -s tests -p 'test_google_calendar.py' -v
python -m unittest discover -s tests -v
```

Expected: 13 calendar adapter tests and 141 total tests pass. Fake responses exercise 400/401/403/404/redirect classification, legacy and modern disabled-service/scope reasons, unknown reasons, malformed JSON, no retries for permanent failures and redaction of sentinel secrets.

## User acceptance: retry the failing read

Prerequisites: current PR checkout, the same disposable token and explicit test calendar from UAT C/D, and permission to manage the test Cloud project's API settings if required.

1. Rerun the UAT D script unchanged. If the request is rejected, expect a fixed code from the table without a traceback, raw response, token or private identifier. Record the code only.
2. Apply the table's matching remedy in the correct test project/account. For `google_calendar_not_found`, verify the exact test-calendar ID and share that calendar with the authorized account with event-details access if needed. Do not publish the calendar or broaden OAuth scopes to work around access errors.
3. Rerun UAT D. Expect the three-event PASS line once the configuration issue is resolved; verify events remain unchanged in the Google UI. If it still fails, report the new fixed code rather than token contents.
4. Restore any temporary test-account/calendar permissions when finished and follow the acceptance guide's disposable-resource cleanup. No event writes are performed by this diagnostic change.

Record actual diagnostic retries in [the execution record](test-evidence.md), separate from expected steps. Earlier review identified a disabled API and later successful event counts, but those reports do not verify all event shapes, account classes or production paths. Run the applicable current cases and record their actual results; do not carry historical PR status forward as a new acceptance result.
