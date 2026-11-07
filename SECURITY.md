# Security

Do not include credentials, tokens, private calendar events, or database copies in issues. Report security findings privately to the repository owner through GitHub private vulnerability reporting when enabled.

The scaffold has no credential handling or network clients. Future provider implementations must keep tokens in permission-restricted files, use least-privilege OAuth scopes, verify TLS, redact logs, and never expose refresh tokens to a browser UI. Keycloak authentication will not replace Google OAuth or Nextcloud app passwords.
