# Keycloak login and owner-scoped web shell — #41 / #19

This is the **first #19 increment**, following #33. It provides an HTTPS-facing FastAPI login flow, encrypted server-side Keycloak sessions, a read-only account overview and own-record APIs. Calendar account connection (#35/#36), pair configuration (#37), scheduler integration (#34), status/history (#18) and two-way writes (#38) are not enabled. The UI says so explicitly. The independent v0.1.0 customer worker is unchanged; do not replace its image or attach its state for these tests.

## First test: no Keycloak account, server or credentials required

Use the feature checkout on Linux/macOS/WSL (Linux home filesystem), Python 3.12+. From repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
python -m unittest discover -s tests -p 'test_web*.py' -v
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
python scripts/check_runtime_lock.py
python -m pip check
python scripts/smoke_web.py
```

Expected: **31 focused / 247 total tests**, individually documented as WEB-001–022 and OIDC-001–009 in [the catalogue](test-cases.md); **35 runtime pins**, matching package metadata and no broken dependencies. The smoke prints PASS WEB-UAT-01–06 and cleanup confirmation. It drives real ASGI routes, Authlib PKCE/token processing and signature validation using a private RSA-signed **offline test issuer**. No sockets/provider accounts/customer credentials or production state are needed. Temporary synthetic keys, sessions and control records are cleaned automatically. No fake provider mode is available in the application CLI/environment; test injection exists only in fixture code outside the installed app package.

| Case | Exercise / expected result |
| --- | --- |
| WEB-UAT-01 | Private prelogin cookie with Secure/HttpOnly/SameSite/path flags; foreign Origin/CSRF rejected before any code exchange. |
| WEB-UAT-02 | PKCE callback validates signed identity, rotates cookie, provisions exact issuer/subject owner and encrypts server-held tokens. No token response/download. |
| WEB-UAT-03 | Alice sees only owned records; Bob's and unknown IDs both 404; unimplemented pair mutations 405 and capabilities false. |
| WEB-UAT-04 | Access-token refresh preserves rotated grant; unavailable identity server yields sanitized 503 and no protected data; recovered server allows retry. |
| WEB-UAT-05 | Signed backchannel logout invalidates only the matching session; repeated logout is safe and does not create another session. |
| WEB-UAT-06 | CSRF-checked local logout and 30-minute idle expiry deny old browser cookies. No worker/calendar operation occurs. |

Author local/CI results are recorded in the PR with actual source/runtime. Owner execution must be recorded separately. These synthetic PASS lines **do not establish that a real Keycloak realm, tenant policy, proxy or browser is configured correctly**. Live cases below stay Not run until executed against a disposable real realm.

Offline Docker reproduction (build needs registry access; runs have network disabled):

```sh
docker build --build-arg VCS_REF="$(git rev-parse HEAD)" -t calendar-sync:web-login .
docker run --rm --network none --read-only --tmpfs /tmp:rw,mode=1777 \
  --mount "type=bind,src=$PWD/scripts,dst=/checks,readonly" \
  calendar-sync:web-login python /checks/smoke_web.py
docker run --rm --network none --read-only --tmpfs /tmp:rw,mode=1777 \
  --mount "type=bind,src=$PWD/tests,dst=/app/tests,readonly" \
  --mount "type=bind,src=$PWD/scripts,dst=/app/scripts,readonly" \
  --mount "type=bind,src=$PWD/docs,dst=/app/docs,readonly" \
  calendar-sync:web-login python -m unittest discover -s tests -v
```

Expect the same six smoke results and 247 tests under UID10001/Python3.12. Containers/temporary fixtures remove themselves; the local development image remains. No customer secrets/volumes should be mounted here.

## Live setup: use a disposable Keycloak realm first

Live testing requires a real Keycloak administrator, two test users and reachable verified HTTPS addresses. It cannot be completed by running a reserved example hostname. A headless server is supported: users sign in from their laptop browsers, while code exchange/refresh/introspection run on the server. No laptop Python, Java helper or token file transfer is needed for this **application login**. Google Calendar permission/consent remains a later, independent connection feature.

Decide the public HTTPS origin for the new web service and the Keycloak realm issuer. Copy the issuer exactly from the realm's /.well-known/openid-configuration (the issuer ends with the realm name, not the discovery filename). The implementation supports Keycloak's fixed protocol/openid-connect endpoints, not arbitrary OIDC endpoint discovery. DNS, browser certificates and outbound verified HTTPS from the web container must work. The default trust store is used; a private CA requires a read-only public CA bundle mount and WEB_OIDC_CA_FILE pointing to its absolute container path. No TLS-verification-disable or public HTTP exception exists.

Configure a **new confidential OpenID Connect client** in the disposable realm:

1. Choose a client ID, for example calendar-sync. Turn **Client authentication on** and **Standard flow on**. Disable implicit flow, direct access/password grants and service accounts for this client.
2. Set exactly one valid redirect URI: your chosen web origin followed by **/auth/callback**. No wildcard. Web origin: the exact chosen HTTPS origin. Require PKCE **S256** in the client advanced settings. ID-token signing must be **RS256**, with the normal sub and sid claims retained.
3. Configure **Backchannel logout URL** as your web origin followed by **/auth/backchannel-logout**; enable session-required backchannel logout. That endpoint must be reachable from Keycloak, not just from the laptop. It authenticates signed logout tokens, not browser cookies.
4. Create client role **sync-user**. Assign it explicitly to disposable Alice and Bob users; keep an unassigned user for the denial test. Administrative/realm roles alone do not grant application access.
5. Add/confirm a dedicated **User Client Role** mapper for this client. Client ID is your chosen ID; claim name is **resource_access.YOUR_CLIENT_ID.roles** (substitute the real ID), multivalued string, **Add to ID token on**. It must emit the sync-user client role. Do not substitute email, display name or broad realm-admin roles. If using the existing roles client scope, verify its client-role mapper really includes this claim in ID tokens rather than access tokens only.
6. Copy the Keycloak client's secret from its Credentials tab through the protected setup prompt below. This is **not** a Google OAuth JSON file, user password or Nextcloud app password. Use disposable user passwords only on Keycloak's own login page.

## Configure and start the isolated web deployment

From the feature checkout, generate the settings file by answering three prompts with the actual HTTPS origin, realm issuer and client ID. With the native environment already installed for the unit tests above:

```sh
python scripts/configure_web.py
```

On a Docker-only/headless host, use the same prompt tool inside the development image instead; no host application Python installation is needed:

```sh
docker build --build-arg VCS_REF="$(git rev-parse HEAD)" -t calendar-sync:web-login .
docker run --rm -it --network none --read-only --cap-drop ALL \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$PWD,dst=/workspace" --workdir /workspace \
  calendar-sync:web-login python scripts/configure_web.py
```

The only intended repository write is a new private .env.web, owned by the invoking host user. An existing .env.web is refused; review/edit it deliberately rather than reset it. This Docker configuration command and the Compose commands below assume a POSIX Linux/macOS/WSL operator shell, not native Windows Python.

This creates a private, ignored .env.web without overwriting an existing one. It contains URLs/IDs and container file references only; it does not ask for any token or client secret. .env.web.example is a **format reference**, not a working live test environment. Review the actual values in .env.web; keep the default five /data/web and /run/web-secrets paths for the Docker setup helper.

Use a distinct Compose project and only compose.web.yaml; it does not start the existing calendar worker or mount its volume:

```sh
webuat() {
  docker compose --env-file .env.web -p calendar-sync-web-uat -f compose.web.yaml "$@"
}
webuat build
webuat --profile setup run --rm web-init
```

The one-time setup prompts for the **Keycloak client secret without echoing it**, generates separate control/session encryption keys, and installs private files in the web-secrets named volume for UID10001. No provider requests are made. Expected: Web keys and client secret initialized privately for UID10001. It refuses existing key/state files; a failed or partial setup requires inspection, not deletion/reinitialization of a real key or volume. The setup service runs with the limited root capabilities needed for named-volume ownership; the running web service is unprivileged/read-only.

Configure an HTTPS reverse proxy for the chosen WEB_ORIGIN to the server's **127.0.0.1:8000**, retaining the original Host header. For example, a host-installed Caddy can reverse_proxy 127.0.0.1:8000 under the actual public hostname with a valid certificate. Do not expose raw port 8000 publicly, enable query/body access logs, use a wildcard redirect or browse the plain HTTP backend to test login. Secure cookies require browser HTTPS. X-Forwarded headers are ignored by the application; redirects always use the configured origin. Existing proxy configuration is deployment-specific and must be reviewed before changing it.

```sh
webuat up -d web
webuat ps
webuat logs --tail=50 web
```

Open the actual WEB_ORIGIN in your laptop browser. The public page shows Sign in; its form offers Continue with Keycloak. After a permitted user's consent/login, **/app** shows Calendar overview with the signed-in display name. Fresh web state has no connections/pairs; this is expected. The UI clearly says connection/setup/two-way features are not enabled. Closing the browser does not affect the separate existing sync worker. Process health at /healthz means the web process is alive, not that Keycloak or a calendar worker is healthy.

## Real browser acceptance — WEB-LIVE-01–04 (Not run until operator execution)

Record source/image, Keycloak version/realm alias, browser, proxy/TLS arrangement and each outcome in [test-evidence](test-evidence.md). Do not attach tokens, cookies, client secret, full callback URLs or private identifiers. Use only the disposable project/realm/users above.

| Case | Exact user actions | Expected result / cleanup |
| --- | --- | --- |
| WEB-LIVE-01 | Open configured HTTPS origin, click Sign in then Continue with Keycloak, log in as role-assigned Alice. Refresh /app; open /api/v1/me in same browser. | Overview and own identity appear; capabilities for provider connection/pair creation/two-way are false. Browser has only opaque Secure/HttpOnly/SameSite cookie, no token download/localStorage credentials. No calendars change. |
| WEB-LIVE-02 | In a separate private browser log in as Bob; compare own owner_id with Alice without publishing them. Use the unassigned user's private window to attempt login. | Bob is a distinct owner; unassigned user denied access. For seeded disposable records only, Alice's GET of Bob's connection/pair ID must match unknown-ID 404. Offline WEB-014 covers exact fixture seeding; an empty real store alone does not prove that denial case. Leave all test records paused/dry-run. |
| WEB-LIVE-03 | Keep Alice signed in; from Keycloak Admin Console terminate Alice's test session (or disable the test user and terminate sessions). Refresh Alice's /app, then Bob's. Temporarily stop only the disposable Keycloak service/egress and request Bob's protected page, then restore it. | Signed backchannel/introspection revokes Alice; Bob unaffected by Alice-only logout. Keycloak outage yields no protected data (503), not stale success. Restore test connectivity; never stop the production tenant to run this case. Record both backchannel and introspection behavior, not just eventual expiry. |
| WEB-LIVE-04 | Sign in again, wait over access-token lifetime then refresh /app; click Sign out. Open /api/v1/me with old browser state. Repeat fresh login and leave idle over 30 minutes, then request protected data. | Refresh remains server-side; Sign out invalidates local app session and old cookie; idle request denied. Keycloak SSO may still allow a later explicit new login without a password: local sign-out is not global identity-provider logout. No Google/Nextcloud grant or worker state is changed. Restore disposable user/realm settings only. |

Offline tests additionally verify the 8-hour absolute cap, concurrency, wrong JWT claims/signatures, tampered session storage, stale/replayed callbacks and sanitized failures; do not wait eight hours merely to mirror the deterministic absolute-expiry unit case. Tenant-specific role changes are reflected on token refresh; immediate administrative removal should also terminate that user's Keycloak sessions. Administrator session/idle/max-lifetime policies may end access sooner.

Acceptance cleanup: webuat stop web preserves keys/state for inspection; webuat down removes containers/network but retains named volumes. Revoke only this disposable client's test sessions deliberately. Do not use down -v against customer projects, delete real grants or reset engine/control history. Back up control state plus its separate key using the #33 procedures before actual future migrations. Session records are ephemeral and should not be restored to revive revoked grants; a planned session-key rotation requires stopping the web service and expiring its web sessions, not rotating/replacing the control key by accident.

## Security and operating limits

Only server-verified Keycloak issuer/subject creates OwnerContext. Required RS256 signature, issuer/audience/azp, nonce/PKCE/state, expiry/not-before/issued-at and supplied at_hash are validated using Authlib/joserfc. JWKS is fetched only from the configured Keycloak endpoint, cached five minutes with a 30-second failed/unknown-key refresh floor; no JWT-supplied key URL is fetched. Network redirects/proxy/netrc inheritance are disabled; bodies/timeouts are bounded. Fixed safe errors are returned, without codes/tokens/raw upstream responses in logs. Uvicorn access logs/debug are disabled; keep reverse-proxy query/body logs disabled too.

Cookies contain random session handles only. Separate private session SQLite stores hashes and AES-GCM encrypted flow/Keycloak token records with associated row identity/expiry; the session key is separate from the control key. Tokens never enter browser JSON/HTML/downloads. Thirty-minute idle and eight-hour absolute expiry are enforced on the server. Refresh is serialized; protected requests require live fixed-endpoint token introspection plus active local owner lookup. Identity/control unavailability fails closed. Same-origin CSRF checks apply to login initiation/local logout; callback and signed backchannel logout have their own validation/replay controls. Local logout does not stop workers or globally sign out other apps.

One web process/replica owns its private session database lock; do not run multiple Uvicorn workers or force-delete locks. Protected OIDC operations are serialized initially; no throughput/high-availability claim is made. There is a 1,000-record session/flow cap; deployment rate limiting belongs at the reverse proxy. Network timeouts are per operation, not a guaranteed whole-request wall-clock deadline. Session cleanup removes only expired ephemeral flows/sessions/replay entries, never engine or control audit. Keys and control state stay in separate private named volumes; session data is not part of engine recovery. Scope/role/provider access is not inferred from a calendar email.

The read-only overview APIs return at most 100 owned items and a truncated flag (not a complete calendar-discovery API); item lookups check ownership. Configuration pagination and mutation contracts arrive with #37/#18. No UI/API route reads the locked engine database. No generic token export, impersonation, filesystem path or owner_id mutation input exists. Google/Nextcloud connections require their separate future grants even when Keycloak SSO works. Existing v0.1.0 customer release acceptance remains independent.

References: [Keycloak OIDC endpoints/logout](https://www.keycloak.org/securing-apps/oidc-layers), [joserfc claims validation](https://jose.authlib.org/en/guide/jwt/), [FastAPI async tests](https://fastapi.tiangolo.com/advanced/async-tests/).
