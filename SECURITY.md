# Security

Do not include credentials, tokens, private calendar events, or database copies in issues. Report security findings privately to the repository owner through GitHub private vulnerability reporting when enabled.

The default command performs no network access. Explicit Google authorization and read operations use permission-restricted credential files, fixed HTTPS endpoints, read-only event scope and sanitized error codes. See docs/google.md for storage, diagnostics and deployment constraints. Future providers must preserve those safeguards and never expose refresh tokens to a browser UI. Keycloak authentication will not replace Google OAuth or Nextcloud app passwords.
