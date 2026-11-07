# Test execution record template

Copy this template into the PR description or an attached sanitized review record. Do not fill Passed by default. Existing catalogue entries describe expected behavior, not execution history.

| Field | Value to fill |
| --- | --- |
| Issue / PR / tested commit | |
| Tester and date | |
| OS, Python and package/runtime versions | |
| Environment / account class | Offline, UAT personal, UAT Workspace, production personal, production Workspace |
| Selected setup case | SETUP-PT, WT, PP, WP or Not applicable with reason |
| Prerequisites and fixture preparation | Calendar alias only; no private IDs or credentials |

| Case ID / subcase | Command or documented step link | Expected result | Actual result / sanitized evidence | Status | Reason / follow-up |
| --- | --- | --- | --- | --- | --- |
| Catalogue check | `python scripts/check_test_catalogue.py` | All discovered tests documented once | | Not run | |
| Automated cases (list IDs or a complete range) | | | | Not run | |
| Relevant UAT cases (one row per case/subcase) | | | | Not run | |
| Selected SETUP case | | | | Not run | |
| OPS-01 / OPS-02 (separate if results differ) | | | | Not run | |
| DOC-01 / DOC-02 / DOC-03 | | | | Not run | |

Use Passed / Failed / Not run / Not applicable. Add rows until every applicable case is accounted for; list nonapplicable setup paths with reasons. Group only cases with the same actual result. Include assertion failures and their resolution, not just the final happy path. Do not attach tokens, authorization URLs/codes, raw provider payloads, calendar emails or private event titles.

Record any remaining gaps, authorized cleanup performed, restored permissions and rollback notes. Owner acceptance/merge is a separate decision; successful unit tests and a merged PR do not fill missing live UAT results.
