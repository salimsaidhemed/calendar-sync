"""Require one documented case for every test discovered by unittest."""

from collections import Counter
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def test_ids(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_ids(item)
        else:
            yield item.id()


def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    discovered = set(test_ids(suite))
    catalogue = (ROOT / "docs/test-cases.md").read_text()
    entries = re.findall(r"^\| ([A-Z]+-\d{3}) \| `([^`]+)` \|", catalogue, re.MULTILINE)
    documented = Counter(identifier for case_id, identifier in entries)
    case_ids = Counter(case_id for case_id, identifier in entries)
    problems = []
    for identifier in sorted(discovered - documented.keys()):
        problems.append("Undocumented test: " + identifier)
    for identifier in sorted(documented.keys() - discovered):
        problems.append("Stale documented test: " + identifier)
    if any(count != 1 for count in documented.values()):
        problems.append("Test identifiers must be documented exactly once")
    if any(count != 1 for count in case_ids.values()):
        problems.append("Case IDs must be unique")
    if not discovered:
        problems.append("No tests discovered")
    if problems:
        print("\n".join(problems))
        return 1
    print(f"PASS: {len(discovered)} discovered tests documented exactly once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
