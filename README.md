# Calendar Sync

Repository foundation for Calendar Sync v0.1.0: a Dockerized Python worker connecting one Google Workspace master calendar to one Nextcloud CalDAV calendar at `https://nextcloud.tcadc.com`.

**Status: provider/state libraries and dry-run planner implemented; write execution and worker not yet implemented.** The default command validates safe defaults and reports scaffold status; it never contacts either provider. This is not a released v0.1.0 sync engine.

The [SQLite state library](docs/state.md) now provides configuration-scoped mappings, run history, audit records and migrations. It is not yet wired into the CLI; normal scaffold runs do not create a database.

The [Google adapter](docs/google.md) and [Nextcloud adapter](docs/nextcloud.md) supply complete reads and [event normalization](docs/nextcloud.md#event-normalization-and-serialization). The [dry-run planner](docs/planner.md) proposes actions with deterministic identities and ownership/conflict checks. Authorization remains optional during development. Write execution and continuous worker wiring remain future features; no provider writes are available.

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

Start with [Google setup by account/environment](docs/google-setup.md) and [the testing guide](docs/testing.md). The [automated catalogue](docs/test-cases.md) covers every discovered unit test; the [manual acceptance guide](docs/google-acceptance.md) defines exact event fixtures and individual UAT cases. Production Google authorization preparation is documented separately from the still-incomplete production sync worker.

Repository: [salimsaidhemed/calendar-sync](https://github.com/salimsaidhemed/calendar-sync) (private).

All changes use linked issues, feature branches and owner-approved pull requests. Only the owner merges. See [CONTRIBUTING.md](CONTRIBUTING.md), [the roadmap](docs/roadmap.md) and [the changelog](CHANGELOG.md). No license is selected yet; choose one before public distribution.

## Planned implementation

See [the architecture and acceptance criteria](docs/architecture.md). The initial design assumes Google is authoritative and Nextcloud is the destination. Bidirectional sync requires an explicit conflict policy and is not part of this scaffold. FastAPI and Keycloak are future components.
