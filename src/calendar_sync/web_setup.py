"""First-time Docker named-volume setup. No provider requests or credential echo."""

import getpass
import os
import stat
from pathlib import Path

from calendar_sync.control import ControlError, create_key, private_file, _path
from calendar_sync.web_oidc import WebSettings


def initialize(settings,secret,*,uid):
    if not isinstance(secret,str) or not secret.strip() or len(secret)>4096:
        raise ControlError('web_setup_secret_required')
    files = [Path(settings.control_key_file),Path(settings.session_key_file),Path(settings.client_secret_file)]
    if len({path.parent for path in files})!=1 or any(os.path.lexists(path) for path in files):
        raise ControlError('web_setup_existing_or_inconsistent_secrets')
    if any(os.path.lexists(path) for path in (settings.control_file,settings.session_file)):
        raise ControlError('web_setup_existing_state')
    directories = {Path(settings.control_file).parent,Path(settings.session_file).parent,files[0].parent}
    # This command owns only fresh, empty application directories; never chmod existing state.
    for directory in directories:
        if uid==os.geteuid():
            _path(directory)
        else:
            # The fresh /data volume belongs to runtime UID10001. The root-only
            # initializer may prepare that approved owner's private descendants.
            if os.geteuid()!=0:
                raise ControlError('web_setup_owner_required')
            for ancestor in (directory,*directory.parents):
                if ancestor.is_symlink():
                    raise ControlError('web_setup_unsafe_directory')
                if not ancestor.exists():
                    continue
                info = ancestor.stat()
                sticky_root = info.st_uid==0 and info.st_mode & stat.S_ISVTX
                if info.st_uid not in (0,uid) or (info.st_mode & 0o022 and not sticky_root):
                    raise ControlError('web_setup_unsafe_directory')
        if directory.exists() and any(directory.iterdir()):
            raise ControlError('web_setup_directory_not_empty')
    for directory in directories:
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        directory.chmod(0o700)
    create_key(files[0])
    create_key(files[1])
    with os.fdopen(private_file(files[2],create=True,exclusive=True,writable=True),'w') as stream:
        stream.write(secret.strip())
        stream.flush()
        os.fsync(stream.fileno())
    gid = os.getegid() if uid==os.geteuid() else uid
    # /data/web is created by this setup for the two private database directories.
    parents = set()
    for directory in directories:
        if directory.parent.name=='web':
            parents.add(directory.parent)
    for path in [*files,*directories,*parents]:
        os.chown(path,uid,gid)
    for path in files:
        path.chmod(0o600)


def main():
    try:
        if os.geteuid()!=0:
            raise ControlError('web_setup_run_compose_setup_profile')
        settings = WebSettings.from_env(os.environ)
        # Exact container paths prevent a root initializer being aimed at arbitrary files.
        expected = ('/data/web/control/control.sqlite3','/data/web/sessions/sessions.sqlite3',
            '/run/web-secrets/control.key','/run/web-secrets/session.key','/run/web-secrets/client-secret')
        actual = (settings.control_file,settings.session_file,settings.control_key_file,
                  settings.session_key_file,settings.client_secret_file)
        if actual!=expected:
            raise ControlError('web_setup_use_default_container_paths')
        initialize(settings,getpass.getpass('Keycloak client secret (not echoed): '),uid=10001)
        print('Web keys and client secret initialized privately for UID10001; no provider requests')
        return 0
    except Exception:
        print('web_setup_failed; preserve existing files and inspect setup before retrying')
        return 2


if __name__=='__main__':
    raise SystemExit(main())
