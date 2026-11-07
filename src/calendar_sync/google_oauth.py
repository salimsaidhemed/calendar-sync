"""Local Google authorization and serialized, atomic token refresh."""

from contextlib import contextmanager
import fcntl
from functools import partial
import http.client
import json
import logging
import os
from pathlib import Path
import stat
import tempfile

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import requests


SCOPES = ("https://www.googleapis.com/auth/calendar.events.readonly",)
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
AUTH_URIS = {AUTH_URI, "https://accounts.google.com/o/oauth2/v2/auth"}


class GoogleError(RuntimeError):
    """Messages contain fixed reason codes, never upstream errors or payloads."""


@contextmanager
def quiet_network():
    previous = logging.root.manager.disable
    http_debug = http.client.HTTPConnection.debuglevel
    https_debug = http.client.HTTPSConnection.debuglevel
    logging.disable(logging.CRITICAL)
    http.client.HTTPConnection.debuglevel = 0
    http.client.HTTPSConnection.debuglevel = 0
    try:
        yield
    finally:
        http.client.HTTPConnection.debuglevel = http_debug
        http.client.HTTPSConnection.debuglevel = https_debug
        logging.disable(previous)


def read_private_json(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor) as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077
                or metadata.st_uid != os.geteuid()):
            raise GoogleError("google_secret_permissions")
        value = json.load(stream)
        if not isinstance(value, dict):
            raise GoogleError("google_secret_format")
        return value


class TokenStore:
    def __init__(self, path):
        self.path = Path(path).absolute()

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = self.path.parent.stat()
        if metadata.st_mode & 0o077 or metadata.st_uid != os.geteuid():
            raise GoogleError("google_token_directory_permissions")
        descriptor = os.open(str(self.path) + ".lock",
                             os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.is_symlink():
                raise GoogleError("google_token_symlink")
            yield
        finally:
            os.close(descriptor)

    def save(self, credentials):
        descriptor, temporary = tempfile.mkstemp(prefix=".oauth-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(credentials.to_json())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def validate_credentials(credentials):
    if (not credentials.refresh_token or not credentials.token
            or not credentials.expiry or set(credentials.scopes or ()) != set(SCOPES)
            or (credentials.granted_scopes is not None
                and set(credentials.granted_scopes) != set(SCOPES))):
        raise GoogleError("google_authorization_scope_or_token")


def authorize(client_file, token_file):
    try:
        if Path(client_file).resolve() == Path(token_file).resolve():
            raise GoogleError("google_distinct_secret_files_required")
        with quiet_network(), TokenStore(token_file).locked():
            config = read_private_json(client_file)
            installed = config.get("installed", {})
            if (not isinstance(installed, dict) or not installed.get("client_id")
                    or not installed.get("client_secret")
                    or installed.get("auth_uri") not in AUTH_URIS
                    or installed.get("token_uri") != TOKEN_URI):
                raise GoogleError("google_desktop_client_required")
            flow = InstalledAppFlow.from_client_config(
                {"installed": installed}, scopes=SCOPES, autogenerate_code_verifier=True
            )
            try:
                flow.oauth2session.trust_env = False
                flow.oauth2session.request = partial(
                    flow.oauth2session.request, timeout=30, allow_redirects=False
                )
                credentials = flow.run_local_server(
                    host="127.0.0.1", bind_addr="127.0.0.1", port=0,
                    open_browser=True, authorization_prompt_message=None,
                    success_message="Authorization complete. You may close this window.",
                    timeout_seconds=180, prompt="consent", access_type="offline",
                    include_granted_scopes="false",
                )
            finally:
                flow.oauth2session.close()
            validate_credentials(credentials)
            TokenStore(token_file).save(credentials)
    except GoogleError:
        raise
    except Exception:
        raise GoogleError("google_authorization_failed") from None


class GoogleTokens:
    def __init__(self, token_file):
        self.store = TokenStore(token_file)

    def access_token(self):
        try:
            with quiet_network(), self.store.locked():
                info = read_private_json(self.store.path)
                if info.get("token_uri") != TOKEN_URI or set(info.get("scopes", ())) != set(SCOPES):
                    raise GoogleError("google_authorization_scope_or_endpoint")
                credentials = Credentials.from_authorized_user_info(info, scopes=SCOPES)
                validate_credentials(credentials)
                if not credentials.valid:
                    with requests.Session() as session:
                        session.trust_env = False
                        request = Request(session=session)
                        credentials.refresh(partial(request, timeout=30, allow_redirects=False))
                    validate_credentials(credentials)
                    if not credentials.valid:
                        raise GoogleError("google_refresh_failed")
                    self.store.save(credentials)
                return credentials.token
        except GoogleError:
            raise
        except FileNotFoundError:
            raise GoogleError("google_authorization_required") from None
        except Exception:
            raise GoogleError("google_refresh_or_storage_failed") from None
