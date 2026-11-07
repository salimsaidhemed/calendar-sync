# Calendar Sync

Repository foundation for Calendar Sync v0.1.0: a Dockerized Python worker connecting one Google Workspace master calendar to one Nextcloud CalDAV calendar at `https://nextcloud.tcadc.com`.

**Status: scaffold only. No calendar connections or sync operations are implemented yet.** Running the command validates the safe defaults and reports this status; it never contacts either provider. This is not a released v0.1.0 sync engine.

## Local development

Python 3.12 or newer:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
calendar-sync
```

No real credentials are needed. Copy `.env.example` to `.env` when preparing deployment; local Python does not automatically load that file. Compose reads it through `env_file`.

```sh
cp .env.example .env
docker compose build
docker compose run --rm worker
```

The scaffold exits after its check. The eventual worker will poll continuously; automatic restart is deliberately disabled until that worker exists. Docker uses an unprivileged account and a named state volume. Never commit credentials, OAuth tokens, event data, or SQLite files.

## Development workflow

Repository: [salimsaidhemed/calendar-sync](https://github.com/salimsaidhemed/calendar-sync) (private).

All changes use linked issues, feature branches and owner-approved pull requests. Only the owner merges. See [CONTRIBUTING.md](CONTRIBUTING.md), [the roadmap](docs/roadmap.md) and [the changelog](CHANGELOG.md). No license is selected yet; choose one before public distribution.

## Planned implementation

See [the architecture and acceptance criteria](docs/architecture.md). The initial design assumes Google is authoritative and Nextcloud is the destination. Bidirectional sync requires an explicit conflict policy and is not part of this scaffold. FastAPI and Keycloak are future components.
