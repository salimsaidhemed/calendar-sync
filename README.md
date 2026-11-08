# Calendar Sync

Repository foundation for Calendar Sync v0.1.0: a Dockerized Python worker connecting one Google Workspace master calendar to one Nextcloud CalDAV calendar at `https://nextcloud.tcadc.com`.

**Status: provider/state libraries, planner, conditional executor and continuous Docker worker implemented.** The bare command remains an offline check; `calendar-sync worker` polls configured calendars, in dry-run by default. This is not yet a released v0.1.0 application; complete live/deployment acceptance remains under #14/#16.

The [SQLite state library](docs/state.md) provides configuration-scoped mappings, run history, audit records, recovery intents and migrations. The worker owns its database lock for its lifetime; bare offline checks do not create a database.

The [Google adapter](docs/google.md) and [Nextcloud adapter](docs/nextcloud.md) supply complete reads and [event normalization](docs/nextcloud.md#event-normalization-and-serialization). The [planner](docs/planner.md) and [executor](docs/execution.md) provide conditional writes and durable crash recovery. The [worker setup and acceptance guide](docs/worker.md) documents polling, health, signals, mounted credentials and deployment. Development/tests need no credentials; actual worker cycles need authorized provider files and explicit calendars.

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
docker compose run --rm worker calendar-sync
```

That command explicitly runs the offline check. For continuous operation, prepare the private mounted credential directories and settings in [worker.md](docs/worker.md), then `docker compose up -d worker`. Compose defaults to dry-run, uses an unprivileged read-only container, persistent state, health checks and restart unless stopped. Never commit credentials, OAuth tokens, event data or SQLite files.

## Development workflow

Start with [Google setup by account/environment](docs/google-setup.md) and [the testing guide](docs/testing.md). The [automated catalogue](docs/test-cases.md) covers every discovered unit test; the [manual acceptance guide](docs/google-acceptance.md) defines exact event fixtures and individual UAT cases. Production Google authorization preparation is documented separately from the still-incomplete production sync worker.

Repository: [salimsaidhemed/calendar-sync](https://github.com/salimsaidhemed/calendar-sync). Current main protections and the review-identity limitation are documented in [branch-protection.md](docs/branch-protection.md).

All changes use linked issues, feature branches and owner-approved pull requests. Only the owner merges. See [CONTRIBUTING.md](CONTRIBUTING.md), [the roadmap](docs/roadmap.md) and [the changelog](CHANGELOG.md). No license is selected yet; choose one before public distribution.

## Planned implementation

See [the architecture and acceptance criteria](docs/architecture.md). The initial design assumes Google is authoritative and Nextcloud is the destination. Bidirectional sync requires an explicit conflict policy and is not part of this scaffold. FastAPI and Keycloak are future components.
