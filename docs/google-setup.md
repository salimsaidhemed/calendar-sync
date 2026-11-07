# Google Calendar and OAuth setup

This guide covers personal Google accounts (including non-Workspace Google accounts), Google Workspace accounts, test environments and production authorization. Reviewed against the linked Google documentation on 2026-11-07. Console wording can change; the selected Cloud project, OAuth client, account and calendar must stay consistent.

**Current capability:** local Desktop OAuth authorization and read-only calendar access. The continuous sync worker, Nextcloud adapter and production deployment wiring are not implemented. Preparing production authorization does not make the current scaffold a production sync service; live sync remains refused.

## Choose the account and environment

The Cloud project owner configures the application. The calendar account authorizes event access. A Workspace administrator controls organization policy. These can be different people/accounts. An email address alone does not establish whether a Cloud project belongs to a Workspace organization.

| Setup case | Audience and publishing choice | Account access | Token implications |
| --- | --- | --- | --- |
| SETUP-PT: Personal account, testing | External, Testing | Add the exact sign-in account under Test users | Calendar-scope refresh grants expire after seven days; reauthorize for longer testing. |
| SETUP-WT: Workspace account, testing | Internal if project belongs to the intended organization and only its users need access; otherwise External, Testing | Internal: organization membership and admin policy. External: exact test-user allowlist entry plus admin policy. | Internal does not use the External Testing seven-day rule; tokens can still be revoked/expire. External Testing retains that rule even for Workspace users. |
| SETUP-PP: Personal account, production authorization | External, In production | Review applicable verification requirements or a valid personal-use exemption | Publishing does not create permanent tokens or automatically verify the app. Issue a fresh production grant after configuration is final. |
| SETUP-WP: Workspace account, production authorization | Internal for same-organization-only use; External, In production for mixed/outside-organization users | Internal organization restriction or External rules, plus Workspace app controls | Internal is not publicly available; External follows production verification/exemption rules. Tokens remain revocable. |

Internal audience requires an eligible organization-associated project; a personal Gmail account cannot authorize an Internal app. A Workspace account using an External project still follows External publishing/test-user rules. See [Google's audience comparison](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview) and [audience controls](https://support.google.com/cloud/answer/15549945?hl=en).

## Shared preparation: all four setup cases

1. Install Python 3.12+ on Linux/macOS and install this repository in a virtual environment using [the test instructions](testing.md). Run authorization on the workstation with your browser, not in a headless container.
2. Create separate Cloud projects for UAT and production. Record each project's ID and the matching OAuth client in an operator-controlled record. Use separate calendars, downloaded client files and token files for each environment.
3. In the intended Cloud project, open **APIs & Services → Library → Google Calendar API → Enable**. Reopen its [API page](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com) in that same project and confirm it is enabled. Adding OAuth scopes does not enable the API.
4. Configure **Google Auth Platform → Branding** with the application name, support email and contact details. Configure **Audience** using the selected path below. In **Data Access**, add exactly `https://www.googleapis.com/auth/calendar.events.readonly`. Do not add write or Gmail scopes.
5. Open **Google Auth Platform → Clients → Create client → Desktop app**. Download the client JSON for this environment. This implementation does not use a Web client, Nextcloud callback, service account or domain-wide delegation. The local library supplies an ephemeral loopback redirect and PKCE; no public redirect endpoint is needed.
6. Confirm that the sign-in account can open the intended calendar and see event details. For a calendar owned by someone else, have the owner share it explicitly with that account; free/busy-only access is insufficient for this feature's event-detail tests. Workspace sharing policy may restrict this.

Google's [Desktop OAuth guide](https://developers.google.com/identity/protocols/oauth2/native-app) and [Calendar Python quickstart](https://developers.google.com/workspace/calendar/api/quickstart/python) describe API/client preparation. This project deliberately requests event-read scope and does not use the quickstart's token deletion or broad scope examples.

## SETUP-PT: personal Google account testing

1. Use the UAT Cloud project from shared preparation; set **Audience → External → Testing**.
2. Under **Audience → Test users → Add users**, add the exact Google account you will select in the browser and save. Project ownership is not a substitute for test-user membership.
3. Download that project's Desktop client to `secrets/google-uat-client.json`, then use the UAT authorization block below.
4. The testing/unverified warning is expected for your own configured test application. Check the application/account before continuing. A hard access-blocked screen usually requires fixing the test-user entry, not publishing to production as a workaround.
5. Create the disposable calendar below and run the complete [Google acceptance cases](google-acceptance.md). After seven days an External Testing calendar grant needs reauthorization; this is not evidence of a broken refresh implementation.

## SETUP-WT: Workspace account testing

1. For an organization-owned project and organization-only testers, select **Internal**. There is no External test-user list to maintain for Internal audience; use a dedicated organization test account and satisfy administrator controls.
2. If Internal is unavailable, or testers include personal/outside accounts, select **External → Testing** and add every exact test sign-in account. A Workspace email does not exempt an External app from test-user rules.
3. Ask an authorized Workspace administrator to ensure Calendar is enabled for the test user and to review **Security → Access and data control → API controls → Manage App Access** for the exact Desktop client ID. Apply the necessary read scope to the intended test users/organizational unit; do not blanket-trust unrelated clients. Tenant policy may still block consent.
4. Download the UAT Desktop client, authorize using the Workspace test account, then create/use the dedicated calendar and acceptance fixtures. External/cross-domain calendar sharing may need separate administrator approval.

Workspace app access controls are independent of Cloud project API enablement and calendar sharing. See [administrator app controls](https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data).

## Create and select the calendar

In the browser, sign into the account intended for this environment. In Google Calendar, next to **Other calendars**, select **+ → Create new calendar**. For UAT, name it `Calendar Sync UAT`, choose a timezone explicitly (for example Europe/Madrid), and use it only for the documented fixtures. For production, select or create the actual master calendar without adding test events. See [Google's calendar creation instructions](https://support.google.com/calendar/answer/37095?hl=en).

Open **Settings → Settings for my calendars → the selected calendar → Integrate calendar** and copy **Calendar ID** exactly. Do not use its display name, browser link, public URL, secret iCal URL or OAuth client ID. Ensure each event you create is assigned to this calendar, not the account's default calendar. Record the ID privately; do not publish calendar feeds to solve access errors.

## UAT authorization: complete workstation commands

Run every line from the repository root with the virtual environment activated. First place the downloaded UAT Desktop JSON at `secrets/google-uat-client.json`; the command does not create a Cloud client for you.

```sh
mkdir -p secrets
chmod 700 secrets
chmod 600 secrets/google-uat-client.json
calendar-sync authorize-google \
  --client-file "$PWD/secrets/google-uat-client.json" \
  --token-file "$PWD/secrets/google-uat-token.json"
```

Select the intended test account, grant read-only event access and wait for terminal success: `Google authorization saved. No calendar events were modified.` The browser alone showing consent is not proof that the token was saved. The process listens on 127.0.0.1 at an ephemeral port for up to 180 seconds; it never prints a login URL or code.

```sh
export GOOGLE_UAT_TOKEN_FILE="$PWD/secrets/google-uat-token.json"
export GOOGLE_UAT_CALENDAR_ID='replace-with-the-exact-uat-calendar-id'
test -f "$GOOGLE_UAT_TOKEN_FILE"
```

Replace only the calendar-ID placeholder. The file check must exit 0. These variables are inputs to the documentation scripts, not automatically loaded worker configuration. The client and token files are different files. Do not paste either file's contents into issues. See [token security/rotation](google.md#token-storage-and-refresh) and [error recovery](google-troubleshooting.md).

## SETUP-PP: personal Google account production authorization

1. Use a separate production Cloud project, enable Calendar API, configure the same minimal scope and create its own Desktop client. Select **External** and **In production** in Audience after reviewing the intended use.
2. Publishing and verification are separate. For a broadly distributed app requesting sensitive/restricted scopes, follow Google's applicable verification requirements before rollout. Google documents personal-use and development exceptions; a limited personal deployment may qualify, but do not describe that as Google verification or unlimited distribution. Check the scope category and applicable exception in the production project's console.
3. Where verification applies, prepare accurate branding, owned/verified domains, homepage/privacy information and a demonstration of the current OAuth/data use as required by Google. Publishing a private GitHub repository is neither required nor sufficient for OAuth verification. Do not claim future UI features in a scope justification.
4. Download the production client to `secrets/google-prod-client.json` and use the production authorization block below. Do not copy the UAT token, assume a Testing-issued grant became permanent, or place UAT fixtures on the master calendar.

See [verification requirements](https://support.google.com/cloud/answer/13464321?hl=en) and [verification exemptions](https://support.google.com/cloud/answer/13464323?hl=en). Resolve your actual audience/verification status before relying on unattended operation; this guide does not grant an exemption.

## SETUP-WP: Workspace production authorization

1. For users solely inside the project's Workspace organization, use a separate production organization-owned project with **Internal** audience. Internal apps do not require the External verification path, but administrator policy and calendar permission checks still apply.
2. Have the administrator approve the exact production client/read scope for the intended users or organizational units; the UAT client's approval does not approve the production client. Ensure the runtime account remains enabled and retains event-detail access to the master calendar.
3. If you must serve personal accounts or other organizations, choose **External → In production** and follow SETUP-PP's verification/exemption steps. Each participating Workspace organization can impose its own access policy.
4. Authorize the designated production calendar account using the separate production Desktop client. User consent is still required by this implementation; administrator approval does not provision a refresh token. There is no service-account impersonation flow here.

## Production authorization and read-only smoke check

This prepares credentials for a future worker; it does not deploy one. After placing the downloaded production client in the stated path, run on the browser workstation:

```sh
mkdir -p secrets
chmod 700 secrets
chmod 600 secrets/google-prod-client.json
calendar-sync authorize-google \
  --client-file "$PWD/secrets/google-prod-client.json" \
  --token-file "$PWD/secrets/google-prod-token.json"
export GOOGLE_PROD_TOKEN_FILE="$PWD/secrets/google-prod-token.json"
export GOOGLE_PROD_CALENDAR_ID='replace-with-the-exact-master-calendar-id'
```

Only an operator authorized to read that production calendar should run this smoke check. Do not use UAT fixture assertions on production data. Copy the entire shell block, including `python - <<'PY'` and the final unindented `PY`:

```sh
python - <<'PY'
import os
from calendar_sync.google_calendar import GoogleCalendar
from calendar_sync.google_oauth import GoogleError

try:
    events = GoogleCalendar(
        os.environ['GOOGLE_PROD_TOKEN_FILE'], os.environ['GOOGLE_PROD_CALENDAR_ID']
    ).list_events()
except GoogleError as error:
    raise SystemExit(str(error)) from None
active_count = sum(event.get('status') != 'cancelled' for event in events)
print(f'PASS: complete read; {active_count} active records; no writes performed')
PY
```

Expected: exit 0 and only an aggregate count, with no event details, browser login or writes. Zero active records can be legitimate; this check alone does not prove that you selected the intended calendar. Verify its ID/ownership independently in the UI. Full reads may hit configured limits; a failure is never accepted as a partial success.

## Token lifecycle, host transfer and operational readiness

The requested scope can read events on calendars available to the consenting account; the configured calendar ID limits this adapter's request, not the OAuth grant itself. External Testing calendar grants expire after seven days. Production/Internal grants avoid that particular testing rule but may still expire or be revoked due to user action, inactivity, token limits or organization policy. Keep an explicit reauthorization procedure; never promise permanent tokens. See [Google's refresh-token rules](https://developers.google.com/identity/protocols/oauth2#expiration).

After authorization, transfer production credentials through a protected channel to a dedicated trusted directory owned by the eventual worker user. Restrict directories to 0700 and files to 0600. The token directory must be writable for atomic token replacement and lock creation; a read-only token-file mount prevents refresh. Container UID is 10001 in the current Dockerfile. A production token is a secret, not an application setting to paste into Compose or an image layer. Protect backups and do not run competing clients against the same token directory.

Do not keep reauthorizing to debug normal reads: repeated grants can invalidate older tokens. After switching environment/client/audience, create a fresh grant deliberately and retire only the old environment's token when safe. Revocation in Google can affect grants beyond a single local file; do not use production grant revocation as UAT cleanup. Rotating/deleting a client requires a planned reauthorization and rollback process.

The current Compose service is an offline scaffold. Before any production sync rollout, the remaining engine/operations issues must supply provider integration, persistent SQLite state, ownership checks, dry-run plan review, conflict handling, health monitoring, backups, restart policy and rollback instructions. Until then, the production smoke above tests Google read authorization only. Never set a hypothetical environment variable to bypass the CLI's live-sync refusal.

## Setup acceptance and evidence

For each applicable SETUP-PT/WT/PP/WP path record: operator/date/commit, account class (no email required), project/environment alias, API-enabled check, audience/publishing selection, client type, account calendar access, authorization result, secret permissions and read-smoke result. Do not attach JSON credentials, tokens or private identifiers. Mark irrelevant paths Not applicable with a reason and unperformed paths Not run; a personal-account success does not prove Workspace administrator policy or production readiness.

Use [test cases](test-cases.md), [manual Google cases](google-acceptance.md) and [the evidence template](test-evidence.md) to record results. No live account/project setup has been performed by the agent for this documentation change.
