import contextlib
import io
import unittest
from unittest.mock import patch

from calendar_sync.cli import main
from calendar_sync.config import Settings


class SafetyTests(unittest.TestCase):
    def test_defaults_are_dry_run(self):
        self.assertTrue(Settings.from_env({}).dry_run)

    def test_ambiguous_boolean_rejected(self):
        for value in ("", "yes", "1", "tru"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings.from_env({"SYNC_DRY_RUN": value})

    def test_insecure_or_secret_bearing_urls_rejected(self):
        for url in ("http://nextcloud.tcadc.com", "https://user:password@example.com", "https://example.com?token=secret", "https://example.com#secret", "https://"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Settings.from_env({"NEXTCLOUD_URL": url})

    def test_live_mode_fails_closed(self):
        with patch.dict("os.environ", {"SYNC_DRY_RUN": "false"}, clear=True):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([]), 2)

    def test_scaffold_reports_no_sync(self):
        output = io.StringIO()
        with patch.dict("os.environ", {}, clear=True):
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([]), 0)
        self.assertIn("sync not implemented", output.getvalue())
