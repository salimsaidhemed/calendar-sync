# Contributing

## Issue to pull request

1. Select an issue from its milestone and review its parent epic and dependencies.
2. Start from current `origin/main` and create `feature/<issue>-<short-description>` (or `fix/<issue>-<description>`).
3. Implement the scoped change, tests and documentation together. Update CHANGELOG.md under Unreleased for shipped changes. For planning-only changes, record them in the roadmap or workflow docs and explain any changelog exemption.
4. Run relevant tests. Record exact results and any unavailable checks in the PR.
5. Open a PR against main with linked issues, change description, Unit tests and User acceptance tests sections, and limitations. Use `Closes #N` only when all acceptance criteria are met; otherwise use `Refs #N`.
6. Wait for the repository owner to approve and merge. Agents must not merge or enable auto-merge, even if checks pass.

No direct commits or pushes to main. A single empty bootstrap commit was authorized to create a PR base in the previously empty repository; it is not an ongoing exception.

## Verification

Follow [the testing procedure](docs/testing.md). Before implementation, map each acceptance criterion to existing or proposed test-case IDs; before review, update [the full automated catalogue](docs/test-cases.md) and relevant manual cases. Each case needs prerequisites, concrete inputs/fixtures, executable steps, expected results including negative paths, state effects and cleanup. Use exact event editor controls, not titles alone, and complete shell/Python command blocks. Record each applicable manual subcase with the [evidence template](docs/test-evidence.md); a PR merge does not establish UAT success.

```sh
python -m pip install -e .
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
calendar-sync
docker build -t calendar-sync:test .
docker run --rm --network none --read-only calendar-sync:test
```

The current CLI only validates scaffold configuration; it does not sync events. Provider integration tests must use fake services unless a dedicated test calendar is explicitly authorized.

Every PR must give the reviewer reproducible testing instructions, not just a statement that tests pass. The Unit tests section includes environment/setup requirements, focused feature tests, full-suite commands, expected results and actual execution evidence. The User acceptance tests section gives user-visible scenarios with exact steps, expected success/failure behavior, required credentials/sample data, state effects and cleanup. Link a feature-specific guide if the steps are lengthy, but summarize scenarios and execution status in the PR. For example, see [Google acceptance testing](docs/google-acceptance.md).

Mark manual scenarios passed, failed or not run with a reason. Unit/CI results cannot be substituted for real-provider UAT. Live checks use explicitly selected disposable test resources and are never assumed authorized solely because a PR contains instructions. Documentation-only PRs still include reviewer inspection steps and may explain why unit tests are not applicable. PRs created through tools must populate both sections explicitly, since tools may bypass the GitHub template.

## Branch protection

The owner manages main protection. Require pull requests and the `test` CI check, block direct pushes and force pushes, and require review where the account plan and reviewer identities allow it. GitHub does not permit authors to approve their own PRs: when the agent publishes using the owner's account, owner review is a manual gate followed by an owner merge, or needs a separate contributor identity for enforceable approval. Never bypass or weaken protection to complete work. Private-repository protection availability depends on the GitHub plan. Track verification in issue #8.

On 2026-11-08 the repository is public and its active ruleset requires PRs, blocks deletion/force pushes and has no bypass actors. The required GitHub Actions `test` check (integration 15368) and up-to-date branch requirement were added and verified through the effective main rules API. Visibility was not changed by this work. See [the verification record](docs/branch-protection.md). Formal approval/agent-merge restrictions remain a shared-identity limitation under #8; do not describe manual owner review as independently enforced.

## Documentation requirements

Document configuration defaults, credential scopes, migrations, compatibility changes and operational recovery as they change. PR descriptions must explain behavior, tests and remaining limitations. Never put credentials or real calendar data in issues, logs, examples or fixtures.
