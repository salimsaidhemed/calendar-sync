from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from calendar_sync.cli import main
from calendar_sync.google_oauth import (
    AUTH_URI, GoogleError, GoogleTokens, SCOPES, TOKEN_URI, TokenStore, authorize,
    quiet_network, read_private_json,
)


def credentials(*, expired=False, scopes=SCOPES):
    return Credentials(
        token="SENTINEL_ACCESS", refresh_token="SENTINEL_REFRESH",
        client_id="fake-client", client_secret="SENTINEL_CLIENT",
        token_uri=TOKEN_URI, scopes=scopes,
        expiry=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(hours=-1 if expired else 1),
    )


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.token_file = Path(self.directory.name) / "google-token.json"
        self.client_file = Path(self.directory.name) / "client.json"
        self.client_file.write_text(json.dumps({"installed": {
            "client_id": "fake-client", "client_secret": "SENTINEL_CLIENT",
            "auth_uri": AUTH_URI, "token_uri": TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }}))
        self.client_file.chmod(0o600)

    def save(self, value):
        store = TokenStore(self.token_file)
        with store.locked():
            store.save(value)

    def test_local_authorization_uses_pkce_loopback_and_does_not_print_secrets(self):
        fake_flow = Mock()
        fake_flow.run_local_server.return_value = credentials()
        output = io.StringIO()
        with patch("calendar_sync.google_oauth.InstalledAppFlow.from_client_config",
                   return_value=fake_flow) as factory:
            with redirect_stdout(output), redirect_stderr(output):
                self.assertEqual(main(["authorize-google", "--client-file", str(self.client_file),
                                       "--token-file", str(self.token_file)]), 0)
        self.assertEqual(factory.call_args.kwargs["scopes"], SCOPES)
        self.assertTrue(factory.call_args.kwargs["autogenerate_code_verifier"])
        arguments = fake_flow.run_local_server.call_args.kwargs
        self.assertEqual(arguments["host"], "127.0.0.1")
        self.assertEqual(arguments["bind_addr"], "127.0.0.1")
        self.assertEqual(arguments["port"], 0)
        self.assertEqual(arguments["access_type"], "offline")
        self.assertIsNone(arguments["authorization_prompt_message"])
        self.assertEqual(self.token_file.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("SENTINEL", output.getvalue())

    def test_valid_token_does_not_refresh(self):
        self.save(credentials())
        with patch.object(Credentials, "refresh") as refresh:
            self.assertEqual(GoogleTokens(self.token_file).access_token(), "SENTINEL_ACCESS")
            refresh.assert_not_called()

    def test_expired_token_refresh_is_persisted(self):
        self.save(credentials(expired=True))

        def refresh(value, request):
            value.token = "ROTATED_ACCESS"
            value._refresh_token = "ROTATED_REFRESH"
            value.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
            self.assertEqual(request.keywords["timeout"], 30)
            self.assertFalse(request.keywords["allow_redirects"])

        with patch.object(Credentials, "refresh", refresh):
            self.assertEqual(GoogleTokens(self.token_file).access_token(), "ROTATED_ACCESS")
        saved = read_private_json(self.token_file)
        self.assertEqual(saved["token"], "ROTATED_ACCESS")
        self.assertEqual(saved["refresh_token"], "ROTATED_REFRESH")

    def test_revoked_refresh_keeps_old_file_and_sanitizes_exception_and_logs(self):
        self.save(credentials(expired=True))
        original = self.token_file.read_bytes()
        log_output = io.StringIO()
        handler = logging.StreamHandler(log_output)
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, old_level)

        def revoked(value, request):
            logging.getLogger("google.auth").error("SENTINEL_REFRESH")
            raise RefreshError("invalid_grant SENTINEL_REFRESH")

        with patch.object(Credentials, "refresh", revoked):
            with self.assertRaises(GoogleError) as caught:
                GoogleTokens(self.token_file).access_token()
        self.assertEqual(str(caught.exception), "google_refresh_or_storage_failed")
        self.assertEqual(log_output.getvalue(), "")
        self.assertEqual(self.token_file.read_bytes(), original)

    def test_missing_authorization_never_starts_browser_flow(self):
        with patch("calendar_sync.google_oauth.InstalledAppFlow") as flow:
            with self.assertRaisesRegex(GoogleError, "google_authorization_required"):
                GoogleTokens(self.token_file).access_token()
            flow.assert_not_called()

    def test_rejects_broad_scope_and_untrusted_token_endpoint(self):
        for field, value in (("scopes", ["https://www.googleapis.com/auth/calendar"]),
                             ("token_uri", "https://example.invalid/token")):
            self.save(credentials())
            info = json.loads(self.token_file.read_text())
            info[field] = value
            self.token_file.write_text(json.dumps(info))
            with patch.object(Credentials, "refresh") as refresh:
                with self.assertRaises(GoogleError):
                    GoogleTokens(self.token_file).access_token()
                refresh.assert_not_called()

    def test_secret_file_permissions_symlinks_and_token_directory(self):
        self.save(credentials())
        self.token_file.chmod(0o644)
        with self.assertRaises(GoogleError):
            GoogleTokens(self.token_file).access_token()
        self.token_file.chmod(0o600)
        alias = self.token_file.with_name("alias-token.json")
        alias.symlink_to(self.token_file)
        with self.assertRaises(GoogleError):
            GoogleTokens(alias).access_token()
        self.token_file.parent.chmod(0o755)
        try:
            with self.assertRaisesRegex(GoogleError, "directory_permissions"):
                GoogleTokens(self.token_file).access_token()
        finally:
            self.token_file.parent.chmod(0o700)

    def test_failed_replace_preserves_old_token_and_cleans_temporary_file(self):
        self.save(credentials())
        original = self.token_file.read_bytes()
        with patch("calendar_sync.google_oauth.os.replace", side_effect=OSError("SENTINEL")):
            with self.assertRaises(OSError):
                self.save(credentials(expired=True))
        self.assertEqual(self.token_file.read_bytes(), original)
        self.assertEqual(list(self.token_file.parent.glob(".oauth-*")), [])

    def test_token_lock_contention_fails_closed(self):
        self.save(credentials())
        with TokenStore(self.token_file).locked():
            with self.assertRaises(GoogleError):
                GoogleTokens(self.token_file).access_token()
        self.assertEqual(GoogleTokens(self.token_file).access_token(), "SENTINEL_ACCESS")

    def test_authorization_failure_preserves_tokens_and_cli_redacts(self):
        self.save(credentials())
        original = self.token_file.read_bytes()
        output = io.StringIO()
        with patch("calendar_sync.google_oauth.InstalledAppFlow.from_client_config",
                   side_effect=RuntimeError("SENTINEL_CLIENT")):
            with redirect_stderr(output):
                self.assertEqual(main(["authorize-google", "--client-file", str(self.client_file),
                                       "--token-file", str(self.token_file)]), 2)
        self.assertEqual(self.token_file.read_bytes(), original)
        self.assertNotIn("SENTINEL", output.getvalue())

    def test_untrusted_client_endpoint_rejected_before_flow(self):
        config = json.loads(self.client_file.read_text())
        config["installed"]["token_uri"] = "https://example.invalid/token"
        self.client_file.write_text(json.dumps(config))
        with patch("calendar_sync.google_oauth.InstalledAppFlow.from_client_config") as factory:
            with self.assertRaises(GoogleError):
                authorize(self.client_file, self.token_file)
            factory.assert_not_called()

    def test_missing_offline_grant_or_extra_granted_scope_keeps_old_token(self):
        self.save(credentials())
        original = self.token_file.read_bytes()
        missing_refresh = credentials()
        missing_refresh._refresh_token = None
        extra_scope = credentials()
        extra_scope._granted_scopes = list(SCOPES) + ['https://www.googleapis.com/auth/calendar']
        for result in (missing_refresh, extra_scope):
            fake_flow = Mock()
            fake_flow.run_local_server.return_value = result
            with patch('calendar_sync.google_oauth.InstalledAppFlow.from_client_config',
                       return_value=fake_flow):
                with self.assertRaises(GoogleError):
                    authorize(self.client_file, self.token_file)
            self.assertEqual(self.token_file.read_bytes(), original)

    def test_client_file_cannot_be_replaced_by_token_file(self):
        with self.assertRaisesRegex(GoogleError, 'distinct_secret_files'):
            authorize(self.client_file, self.client_file)

    def test_logging_controls_restored_after_error(self):
        before = logging.root.manager.disable
        with self.assertRaises(RuntimeError):
            with quiet_network():
                raise RuntimeError("simulated")
        self.assertEqual(logging.root.manager.disable, before)
