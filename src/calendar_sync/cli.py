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
    arguments = parser.parse_args(argv)
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
