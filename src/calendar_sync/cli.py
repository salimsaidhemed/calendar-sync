import os
import sys

from calendar_sync.config import Settings


def main() -> int:
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
    raise SystemExit(main())
