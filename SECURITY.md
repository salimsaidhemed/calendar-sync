# Security

Nextcloud reads use an explicit trusted HTTPS origin/calendar, a private app-password file, refused redirects and bounded safe XML parsing. Server-supplied hrefs are validated but never followed. See docs/nextcloud.md for permissions, read-only acceptance and compatibility limits. Event normalization/serialization has no upload side effects; no provider write methods are available yet. Google and CalDAV share a single-threaded diagnostics boundary in security.py; do not embed these operations in concurrent UI threads.

Do not include credentials, tokens, private calendar events, or database copies in issues. Report security findings privately to the repository owner through GitHub private vulnerability reporting when enabled.

The default command performs no network access. Explicit Google authorization and read operations use permission-restricted credential files, fixed HTTPS endpoints, read-only event scope and sanitized error codes. See docs/google.md for storage, diagnostics and deployment constraints. Future providers must preserve those safeguards and never expose refresh tokens to a browser UI. Keycloak authentication will not replace Google OAuth or Nextcloud app passwords.
