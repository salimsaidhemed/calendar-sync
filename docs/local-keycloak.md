# Local Keycloak login acceptance

This disposable stack addresses #43 and the first #19 increment in PR #42. It supplies its own Keycloak, HTTPS proxy, realm, confidential client, three generated test logins, application keys and separate databases. It needs no customer realm, Google/Nextcloud credentials, laptop Python or `.env.web`. Do not run `configure_web.py` for this option: blank answers there correctly fail HTTPS/client validation.

Prerequisites: Docker with Compose v2 supporting `up --wait`, Git, Bash, registry access, approximately 2 GB free Docker memory and free local ports 8443/9443. Run from the repository checkout on Linux/macOS or WSL2. Pull the latest feature branch first; do not discard local edits. The customer worker can remain separate. These are development tests, not a production Keycloak deployment.

## Start and test

```sh
bash scripts/local_keycloak.sh up
bash scripts/local_keycloak.sh test
```

The first command builds two local images, imports the test realm and waits for Keycloak and the web process. Expect the URLs below and healthy services from `bash scripts/local_keycloak.sh status`. The second command verifies HTTPS using only this fixture's CA and prints six `PASS LOCAL-KC-...` lines, exit 0. It deliberately waits 20 seconds for the token refresh window. It does not install a trusted CA on your computer. Docker startup/first image downloads can take several minutes.

| Address | Purpose |
| --- | --- |
| https://localhost:8443 | Calendar Sync login and read-only overview |
| https://localhost:9443/admin/ | Local Keycloak administration; choose master realm for admin login |
| https://localhost:9443/realms/calendar-sync-uat/.well-known/openid-configuration | Test realm discovery |

Only 127.0.0.1 host ports are published. Keycloak and the web app share the proxy's network namespace, allowing both the issuer and backchannel callback to use these same localhost HTTPS URLs inside containers. No hosts-file changes, host networking or insecure HTTP login mode is needed. The internal proxy upstreams use loopback HTTP; externally visible login URLs and application identity-provider calls use verified HTTPS.

## Browser acceptance

Show the generated **local test only** passwords:

```sh
bash scripts/local_keycloak.sh credentials
mkdir -p .local/keycloak-local
bash scripts/local_keycloak.sh ca > .local/keycloak-local/ca.crt
openssl x509 -in .local/keycloak-local/ca.crt -noout -subject -fingerprint -sha256
```

Do not paste generated passwords, cookies, authorization URLs or tokens into issues. Users **alice** and **bob** have client role `sync-user`; **denied** deliberately lacks it. The separate generated **admin** password is for the local Keycloak console. Passwords persist across restarts; `credentials` retrieves them again. No passwords/client secrets are checked into Git or put in Compose environment values.

Before browser testing, manually trust **this fixture's public CA** in your local test browser/OS. Its subject starts `Calendar Sync Local UAT` followed by a unique fixture identifier. On macOS, import `ca.crt` into your login keychain using Keychain Access, open that specific certificate and set SSL trust to Always Trust. On Windows, import into the current user's Trusted Root Certification Authorities store. Firefox may use its own Authorities store unless configured to use system roots. Verify the fingerprint against the command above. Restart the browser if needed. You perform this local trust decision; the helper does not alter your system trust store or bypass certificate warnings. Remove that specific CA after testing. See [Apple certificate import](https://support.apple.com/guide/keychain-access/add-certificates-to-a-keychain-kyca2431/mac) and [certificate trust](https://support.apple.com/guide/keychain-access/change-the-trust-settings-of-a-certificate-kyca11871/mac).

Open the application URL, click Sign in then Continue with Keycloak, and log in as **alice**. Open a separate private window for **bob** and another isolated session for **denied**. Follow **WEB-LIVE-01–04** in [web-login.md](web-login.md#real-browser-acceptance--web-live-0104-not-run-until-operator-execution). Each allowed user has two synthetic connections needing verification and one paused/dry-run pair. These contain no usable provider credentials; don't try to activate or sync them. The application currently provides login and a read-only overview, not calendar connection or two-way configuration.

For WEB-LIVE-03, use Keycloak Admin Console → calendar-sync-uat realm → Users → alice → Sessions → terminate the test session. Bob must remain signed in. To exercise the **local-only** identity-provider outage subcase:

```sh
docker compose -p calendar-sync-keycloak-local -f examples/keycloak/compose.yaml stop keycloak
# In Bob's browser request /api/v1/me: expect 503 and no protected data.
docker compose -p calendar-sync-keycloak-local -f examples/keycloak/compose.yaml start keycloak
```

Run the automated test before browser acceptance: it terminates Alice's local Keycloak sessions intentionally. For the refresh subcase in WEB-LIVE-04, this realm's access token lasts 45 seconds; wait 50 seconds then refresh. The application idle limit is still 30 minutes. Record browser observations separately; automated HTTP protocol tests do not prove browser cookie, idle or outage behavior.

## Cases and actual status

| Case | Procedure / expected result | Recorded status |
| --- | --- | --- |
| LOCAL-KC-01 | `test`: trusted TLS/discovery issuer, real confidential code/S256 login, assigned client role, connection capabilities disabled | Passed locally with Keycloak 26.8.2 / Caddy 2.11.9 / Docker Python 3.12 |
| LOCAL-KC-02 | `test`: Alice/Bob see only their synthetic records; cross-owner IDs return 404; pair mutation 405; records paused/dry-run | Passed |
| LOCAL-KC-03 | `test`: denied user callback 403, protected API 401 | Passed |
| LOCAL-KC-04 | `test`: wait 20 seconds into 45-second access token; protected calls refresh successfully on server | Passed |
| LOCAL-KC-05 | `test`: admin terminates Alice; signed backchannel deletes her exact app session before any protected request; Alice 401, Bob 200 | Passed |
| LOCAL-KC-06 | `test`: Bob's CSRF-protected app logout succeeds; old local session no longer authenticates | Passed |
| LOCAL-KC-RESTART | Repeat `up`, then `test`: initialization preserves keys/credentials and seeded records; login works again | Passed locally; operator execution remains separate |
| WEB-LIVE-01–04 | Manual browser guide above, including trust/cookies, outage and full idle timeout | Not run by agent; operator execution required |
| Customer realm/provider acceptance | Customer-controlled TLS/roles/SSO policies and future provider connections | Not run; this fixture grants no customer access |

The real-container tests run in CI alongside the offline unit suite. Focused fixture unit cases **LOCAL-001–006** are in [test-cases.md](test-cases.md):

```sh
python -m unittest discover -s tests -p 'test_local_keycloak.py' -v
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
```

Expect 6 focused / 253 total tests and exactly 253 catalogued tests. These unit commands need the development environment from [testing.md](testing.md), but no running Keycloak or credentials.

## Restart, cleanup and failures

```sh
bash scripts/local_keycloak.sh status
bash scripts/local_keycloak.sh logs
bash scripts/local_keycloak.sh down
# Later: preserves credentials, keys, users and test data.
bash scripts/local_keycloak.sh up
```

To permanently discard **only this named disposable stack's** volumes:

```sh
bash scripts/local_keycloak.sh reset --delete-local-test-volumes
```

Remove its exported public CA file and its specific browser/keychain trust entry after testing. Local images remain until deliberately removed. The leaf certificate expires after 30 days; reset/recreate the fixture and trust the newly generated CA when needed. Its CA signing key is never persisted. Initialization refuses partial, tampered or expired fixture state rather than replacing existing keys. Preserve any useful local diagnostic evidence before reset.

The wrapper always selects `calendar-sync-keycloak-local`; advanced parallel runs may set `LOCAL_WEB_UAT_PROJECT=calendar-sync-keycloak-local-SUFFIX` using lowercase letters/digits/hyphens/underscores, but the same host ports cannot be used concurrently. Use the same project variable for every command. Never point this fixture at customer volumes or copy its development settings/passwords into production. It never reads `.env.web`, the customer Compose overlay or provider credential directories.

If startup fails, inspect `status` and `logs`; check port availability, Docker memory, registry access and that you have the latest checkout. `FAIL LOCAL-KC-...` is a failed case; preserve only sanitized case/status output. Do not publish raw Keycloak/proxy logs without review. A browser certificate error requires correct CA trust, not disabling HTTPS. Keycloak 26.6.2+ introspection requires the calling client in the access-token audience: this fixture includes an explicit Audience mapper. For separately administered realms, follow the audience step in [web-login.md](web-login.md). See [Keycloak upgrading guidance](https://www.keycloak.org/docs/latest/upgrading/) and [container guidance](https://www.keycloak.org/server/containers).
