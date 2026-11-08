import os
import sys
import argparse

from calendar_sync.config import Settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Calendar Sync")
    subcommands = parser.add_subparsers(dest="command")
    google_auth = subcommands.add_parser("authorize-google", help="Authorize locally using a browser")
    google_auth.add_argument("--client-file", required=True)
    google_auth.add_argument("--token-file", required=True)
    worker = subcommands.add_parser('worker', help='Poll configured calendars; dry-run by default')
    worker.add_argument('--once', action='store_true', help='Run one cycle and return its health status')
    subcommands.add_parser('health', help='Check the last worker cycle without opening SQLite or providers')
    arguments = parser.parse_args(argv)
    if arguments.command == 'health':
        from calendar_sync.worker import health_ok
        try:
            maximum = int(os.environ.get('SYNC_HEALTH_MAX_AGE_SECONDS', '7200'))
            if maximum <= 0:
                return 1
            return 0 if health_ok(os.environ.get('SYNC_HEALTH_FILE', '/data/health.json'), maximum) else 1
        except Exception:
            return 1
    if arguments.command == 'worker':
        import threading
        from calendar_sync.worker import WorkerSettings, run_worker, shutdown_signals

        try:
            settings = WorkerSettings.from_env(os.environ)
            stop = threading.Event()
            with shutdown_signals(stop):
                return run_worker(settings, once=arguments.once, stop=stop)
        except Exception:
            print('worker_startup_or_state_failed', file=sys.stderr)
            return 2
    if arguments.command == "authorize-google":
        from calendar_sync.google_oauth import GoogleError, authorize

        try:
            authorize(arguments.client_file, arguments.token_file)
        except GoogleError as error:
            print(str(error), file=sys.stderr)
            return 2
        print("Google authorization saved. No calendar events were modified.")
        return 0
    try:
        settings = Settings.from_env(os.environ)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if not settings.dry_run:
        print("Live sync is unavailable in this scaffold.", file=sys.stderr)
        return 2
    print("Calendar Sync scaffold: dry-run enabled; no providers contacted; sync not implemented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
