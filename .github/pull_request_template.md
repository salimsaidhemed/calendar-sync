## Linked issues

Refs #

## Change and reason

Describe resulting behavior and why it is needed.

## Unit tests

Map each acceptance criterion to case IDs in docs/test-cases.md and the relevant manual guide. Add/update catalogue rows for new/changed tests and describe their meaningful subcases. Explain any criterion without an automated case.

Include prerequisites, exact setup and feature-specific test commands, full-suite command, expected results, and actual results (including runtime/version). State which checks are automated in CI. If tests are not applicable, explain why.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
```

Add the focused test command for this feature; do not leave only the generic command above.

## User acceptance tests

Provide reproducible user steps, inline or via a feature-specific testing guide. Include:

- Prerequisites, test environment, required credentials/permissions and sample data.
- Each action/command and its observable expected result, including failure cases.
- State changes, cleanup/rollback and limitations; use disposable test resources for live checks.
- Actual status for each scenario: passed, failed, or not run with a reason. Automated unit tests do not count as manual UAT.
- Stable manual case IDs, exact fixture/editor controls and complete copy/paste blocks; never make users infer event types from titles.
- Applicable account/environment setup cases and explicit Not applicable reasons for others. Use docs/test-evidence.md; do not infer acceptance from merge status.

For documentation-only changes, explain what the reviewer should inspect and what success looks like. Do not leave this section blank or claim unperformed checks passed. Never include secrets or private event data in evidence.

## Documentation and limitations

List updated documentation, changelog entry, compatibility changes and remaining limitations.

## Review checklist

- [ ] Linked issue acceptance criteria addressed or remaining work identified.
- [ ] Relevant tests pass; unavailable checks explained.
- [ ] Reproducible unit-test commands and expected results included.
- [ ] Every discovered test and changed acceptance criterion maps to documented cases; catalogue check passes.
- [ ] UAT steps, expected results, cleanup and actual execution status included.
- [ ] Documentation updated for this change.
- [ ] CHANGELOG.md updated or exemption explained.
- [ ] No secrets or private event data included.
- [ ] Dry-run and ownership/conflict safeguards preserved.
- [ ] Owner review required; owner performs merge. No auto-merge.
