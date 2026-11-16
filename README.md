# Calendar Sync

Calendar Sync development version 0.2.0.dev0 builds on the frozen v0.1.0 one-way worker baseline: a Dockerized Python worker connecting one personal or Google Workspace master calendar to one Nextcloud CalDAV calendar at `https://nextcloud.tcadc.com`.

**Status: one-way worker plus standalone Keycloak login/read-only web shell.** The bare command remains an offline check; `calendar-sync worker` polls configured calendars in dry-run by default. [Web login setup and tests](docs/web-login.md) cover the first M4 UI increment; provider connection/pair setup and two-way writes remain unavailable. See [v0.1.0 release notes](docs/releases/v0.1.0.md), [freeze/publication procedure](docs/release.md) and [GitHub Releases](https://github.com/salimsaidhemed/calendar-sync/releases) for the independent customer worker baseline. Development source is not customer production acceptance.

The [SQLite state library](docs/state.md) provides configuration-scoped mappings, run history, audit records, recovery intents and migrations. The worker owns its database lock for its lifetime; bare offline checks do not create a database.

The [control-store foundation](docs/control-store.md) adds owner-scoped repositories, encrypted credentials, paused calendar-pair reservations and offline legacy import/backup tools. The new web shell reads owned records; the existing worker does not yet consume the control store. Use the credential-free acceptance exercises; keep customer deployments on their approved release/image.

Next development stage: [multi-user web design](docs/multi-user-design.md), [design review and future acceptance](docs/multi-user-acceptance.md), and the [delivery roadmap](docs/roadmap.md). These are proposed contracts; the current release does not yet provide login, user-selected calendar pairs or two-way writes.

The [Google adapter](docs/google.md) and [Nextcloud adapter](docs/nextcloud.md) supply complete reads and [event normalization](docs/nextcloud.md#event-normalization-and-serialization). The [planner](docs/planner.md) and [executor](docs/execution.md) provide conditional writes and durable crash recovery. The [worker setup and acceptance guide](docs/worker.md) documents polling, health, signals, mounted credentials and deployment. Development/tests need no credentials; actual worker cycles need authorized provider files and explicit calendars.

## Local development

Python 3.12 or newer:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
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

For a Google Workspace customer, use [the customer production deployment guide](docs/customer-production-deployment.md), covering administrator authorization, Linux/Docker setup, secure credential transfer, dry-run/live activation and handover. [The setup/backup/recovery runbook](docs/operations.md) provides detailed recovery procedures. [Reproducible Docker UAT](docs/docker-uat.md) supplies the isolated local test template; [reported worker evidence](docs/worker-uat-evidence.md) separates observed runtime checks from missing customer/visual confirmations.

After customer acceptance, follow [Run continuously after customer acceptance](docs/customer-production-deployment.md#run-continuously-after-customer-acceptance) for detached live startup, health verification, reboot prerequisites and maintenance stops while preserving state.

Start with [Google setup by account/environment](docs/google-setup.md) and [the testing guide](docs/testing.md). The [automated catalogue](docs/test-cases.md) covers every discovered unit test; the [manual acceptance guide](docs/google-acceptance.md) defines exact event fixtures and individual UAT cases. Customer acceptance is recorded separately from source-release and automated-test results.

Repository: [salimsaidhemed/calendar-sync](https://github.com/salimsaidhemed/calendar-sync). Current main protections and the review-identity limitation are documented in [branch-protection.md](docs/branch-protection.md).

All changes use linked issues, feature branches and owner-approved pull requests. Only the owner merges. See [CONTRIBUTING.md](CONTRIBUTING.md), [the roadmap](docs/roadmap.md) and [the changelog](CHANGELOG.md). No license is selected yet; choose one before public distribution.

## Planned implementation

See [the architecture and acceptance criteria](docs/architecture.md). Google is authoritative and Nextcloud is the destination. Bidirectional sync requires an explicit conflict policy and is outside v0.1.0. FastAPI/Keycloak/per-user GUI starts with #17, then #19 and #18; [the release boundary](docs/release.md#gui-start-and-release-boundary) keeps those changes separate from the frozen worker.
