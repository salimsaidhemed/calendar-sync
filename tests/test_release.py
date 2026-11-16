from importlib.metadata import PackageNotFoundError
import io
import os
import re
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_runtime_lock


class ReleaseTests(unittest.TestCase):
    def test_runtime_lock_matches_installed_release_and_all_direct_dependencies(self):
        self.assertEqual(check_runtime_lock.check(), 35)

    def test_unpinned_duplicate_and_incomplete_locks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory)/'requirements.lock'
            for text, code in (('requests>=2\n', 'runtime_lock_invalid'),
                               ('requests==2.34.2\nRequests==2.34.2\n', 'runtime_lock_invalid'),
                               ('requests==2.34.2\n', 'runtime_lock_missing_dependency')):
                lock.write_text(text)
                with self.assertRaisesRegex(check_runtime_lock.LockError, '^'+code+'$'):
                    check_runtime_lock.check(lock_file=lock)

    def test_wrong_installed_version_or_missing_package_emits_fixed_code_only(self):
        original = check_runtime_lock.version
        for failure, expected in ((False, 'runtime_lock_version_mismatch'), (True, 'runtime_lock_package_missing')):
            def fake(name):
                if name == 'requests':
                    if failure:
                        raise PackageNotFoundError('SENTINEL_PRIVATE')
                    return '0.0.0'
                return original(name)
            output = io.StringIO()
            with patch.object(check_runtime_lock, 'version', side_effect=fake), redirect_stdout(output):
                self.assertEqual(check_runtime_lock.main(), 2)
            self.assertEqual(output.getvalue().strip(), expected)

    def test_documented_publication_stops_on_tag_push_or_remote_identity_failure(self):
        guide = (Path(__file__).resolve().parents[1]/'docs'/'release.md').read_text()
        block = next(part for part in re.findall(r'```sh\n(.*?)\n```', guide, re.S)
                     if 'git tag -a v0.1.0' in part)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_git = root/'git'
            fake_git.write_text('''#!/bin/sh
case "$1" in
  rev-parse) printf '%s\\n' "$EXPECTED_SHA" ;;
  merge-base|diff) exit 0 ;;
  show-ref) test "$SCENARIO" = local-existing ;;
  ls-remote)
    if test "$#" = 5; then
      if test "$SCENARIO" = remote-existing; then
        printf '%s\\trefs/tags/v0.1.0\\n' "$EXPECTED_SHA"
      fi
    else
      if test "$SCENARIO" = readback-failure; then exit 44; fi
      value="$EXPECTED_SHA"
      if test "$SCENARIO" = wrong-remote; then value=wrong; fi
      printf '%s\\trefs/tags/v0.1.0^{}\\n' "$value"
    fi ;;
  tag) if test "$SCENARIO" = tag-failure; then exit 42; fi ;;
  push) if test "$SCENARIO" = push-failure; then exit 43; fi ;;
  *) exit 99 ;;
esac
''')
            fake_git.chmod(0o700)
            fake_gh = root/'gh'
            fake_gh.write_text('#!/bin/sh\nprintf published > "$PUBLISH_LOG"\n')
            fake_gh.chmod(0o700)
            log = root/'published'
            for scenario in ('local-existing', 'remote-existing', 'tag-failure', 'push-failure',
                             'wrong-remote', 'readback-failure', 'success'):
                log.unlink(missing_ok=True)
                environment = dict(os.environ, FAKE_GIT=str(fake_git), FAKE_GH=str(fake_gh),
                    EXPECTED_SHA='a'*40, RELEASE_SHA='a'*40, SCENARIO=scenario, PUBLISH_LOG=str(log))
                # Invoke stubs through the shell: hardened containers can mount /tmp noexec.
                stubs = 'git() { sh "$FAKE_GIT" "$@"; }; gh() { sh "$FAKE_GH" "$@"; };\n'
                result = subprocess.run(['sh'], input=stubs+block, env=environment, text=True,
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode == 0, scenario == 'success',
                                 (scenario, result.returncode, result.stderr))
                self.assertEqual(log.exists(), scenario == 'success', scenario)
