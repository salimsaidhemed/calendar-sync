# Changelog

## Unreleased

### Added

- Explicit local Google Desktop OAuth authorization with PKCE, loopback redirect and read-only event scope.
- Locked, permission-restricted token files with atomic refresh persistence and sanitized failure codes.
- Google Calendar adapter with complete pagination, bounded retries, duplicate/page-loop detection and fail-closed partial reads.
- Credential-free OAuth/read tests and Google Workspace authorization/setup guide.
- Configuration-scoped SQLite persistence with atomic versioned migrations, worker locking, unique event mappings and run history.
- Append-only audit entries committed atomically with applied mappings; dry-run runs cannot create or advance applied mappings.
- State recovery and integration guide with credential-file references and no stored credential contents.
- Python package scaffold with dry-run defaults and explicit refusal of live mode.
- Dockerfile, Compose example and credential-free CI tests.
- Architecture acceptance criteria for secure OAuth, SQLite mappings, duplicate prevention and conflict handling.
- Five delivery milestones, linked epic/task roadmap, contribution workflow and PR/issue templates.

Calendar synchronization is not yet implemented. No v0.1.0 release has been published.
