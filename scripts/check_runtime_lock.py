"""Check installed package/version pins without providers or credentials."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]


class LockError(RuntimeError):
    pass


def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def check(lock_file=ROOT/'requirements.lock', project_file=ROOT/'pyproject.toml'):
    pins = {}
    for raw in Path(lock_file).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)', line)
        if match is None or normalized(match[1]) in pins:
            raise LockError('runtime_lock_invalid')
        pins[normalized(match[1])] = match[2]
    project = tomllib.loads(Path(project_file).read_text())['project']
    required = {normalized(re.match(r'[A-Za-z0-9][A-Za-z0-9_.-]*', item)[0]) for item in project['dependencies']}
    if not required <= pins.keys():
        raise LockError('runtime_lock_missing_dependency')
    try:
        if version(project['name']) != project['version']:
            raise LockError('release_package_version_mismatch')
        for name, expected in pins.items():
            if version(name) != expected:
                raise LockError('runtime_lock_version_mismatch')
    except PackageNotFoundError:
        raise LockError('runtime_lock_package_missing') from None
    return len(pins)


def main():
    try:
        count = check()
    except (LockError, OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        print(str(error) if isinstance(error, LockError) else 'runtime_lock_check_failed')
        return 2
    print(f'PASS: installed release metadata and {count} runtime pins match')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
