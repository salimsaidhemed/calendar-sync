# Main protection verification — issue #8

Checked 8 November 2026 against the live GitHub API. The repository is currently **public**; the earlier private-repository plan restriction in issue #8 is historical. This work did not change visibility. Public repositories support rulesets on Free; private repository availability depends on plan. Subscription details were not inspected. See [GitHub ruleset availability](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets).

The existing active [protect main ruleset, ID 26740380](https://github.com/salimsaidhemed/calendar-sync/rules/26740380) targets the default branch. Existing requirements were preserved: PRs required, deletion blocked, force pushes blocked, empty bypass list and caller cannot bypass. Added the missing **required status check `test`**, pinned to **GitHub Actions integration 15368**, with strict up-to-date-branch policy. `CI` is the workflow name; `test` is the actual API check context. Auto-merge remains disabled. No direct push, force push or merge was attempted to test enforcement.

## Review identity limitation

The ruleset currently requires zero formal approving reviews, as it did before this change. PRs are authored through `salimsaidhemed`, the same identity used by the owner to merge. [GitHub prohibits authors approving their own PRs](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/reviewing-proposed-changes-in-a-pull-request#submitting-your-review). GitHub also cannot distinguish agent actions from owner actions using the same credentials. Owner-only merging and review are therefore a documented manual policy, not an enforced independent identity boundary. No claim of enforced owner approval or agent-merge denial is made.

Issue #8 remains open for that limitation. To enforce independent owner approval, first provide a separately authenticated contributor/agent identity without merge authority. Then require one approving owner/code-owner review, dismiss stale approvals and resolve conversations; validate those rules with that identity. Do not enable a self-review-impossible approval rule or weaken/bypass existing protection to make a PR pass. No extra account, credential or permission change is performed by this feature.

## Reproducible read-only acceptance

Prerequisites: authenticated GitHub CLI with repository/ruleset read access and network access. These commands neither push nor merge. Run from any directory; no calendar credentials needed.

```sh
gh api repos/salimsaidhemed/calendar-sync --jq '{visibility,default_branch,allow_auto_merge}'
gh api repos/salimsaidhemed/calendar-sync/rulesets/26740380 --jq '{enforcement,conditions,bypass_actors,current_user_can_bypass,rules}'
gh api repos/salimsaidhemed/calendar-sync/rules/branches/main --jq '.[] | {type,parameters}'
gh api repos/salimsaidhemed/calendar-sync/commits/main/check-runs --jq '.check_runs[] | {name,app_id:.app.id,conclusion}'
```

| Case | Expected / actual evidence |
| --- | --- |
| GOV-UAT-01 | Public, default main, auto-merge false. **Passed** API inspection. Visibility was not changed. |
| GOV-UAT-02 | Active default-branch ruleset, empty bypass list; pull_request/deletion/non_fast_forward rules. **Passed** API inspection; no unsafe push probes. |
| GOV-UAT-03 | Effective main rules include required_status_checks: context test, integration 15368, strict true. **Passed** after the narrowly scoped update; all previous rules retained. |
| GOV-UAT-04 | Formal independent owner approval/agent-merge denial. **Not satisfied** with shared author/owner credentials and zero required approvals; tracked in #8. Author self-approval prohibition verified in official documentation; no fabricated review/merge was submitted. |

The updated worker PR must show `test` passing before owner merge. UI review: Settings → Rules → Rulesets → protect main, compare the values above; open the PR and inspect Checks without merging to test. Record checked commit/date and separate API inspection from actual enforcement attempts. Any future plan/visibility change requires re-verification. API access denial is not proof that rules are absent; report it explicitly.

These settings checks have no unit test because they inspect live GitHub account state. Tests for worker code are separate. No destructive negative test is authorized or needed. Cleanup: none; leave protections active. Keep issue #8 open until the identity boundary is resolved and independently verified.
