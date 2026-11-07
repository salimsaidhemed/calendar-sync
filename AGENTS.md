# Repository working agreement

- Start from a linked GitHub issue and work on a feature branch, never main.
- Open a PR with acceptance criteria, test evidence, limitations and documentation updates.
- Every PR must include Unit tests and User acceptance tests sections: prerequisites, exact commands/steps, expected results and actual status. Include feature-specific checks and cleanup; identify unperformed UAT explicitly. Follow .github/pull_request_template.md.
- Every behavior/acceptance criterion must map to documented test-case IDs. Update docs/test-cases.md for every discovered automated test (including subcases), and manual guides for user scenarios. Include exact fixture controls, full copy/paste blocks, failure expectations, cleanup and separate Passed/Failed/Not run/Not applicable evidence. Run scripts/check_test_catalogue.py; do not equate a merge or CI result with live acceptance.
- Do not merge PRs or enable auto-merge. The repository owner approves and merges.
- Document every change. Update CHANGELOG.md for shipped changes and relevant setup or architecture documentation; explain any changelog exemption in the PR.
- Preserve dry-run defaults. Never commit credentials, event data or SQLite state.
- The only authorized direct main commit was the empty repository bootstrap. That exception is exhausted.
