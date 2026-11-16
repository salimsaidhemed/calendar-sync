"""Write public web settings from operator input; never ask for/store a token."""

import os
from pathlib import Path

from calendar_sync.web_oidc import WebSettings


def main():
    target = Path('.env.web')
    if os.path.lexists(target):
        print('Existing .env.web refused; review/edit it deliberately instead of overwriting')
        return 2
    template = Path(__file__).resolve().parents[1]/'.env.web.example'
    values = dict(line.split('=',1) for line in template.read_text().splitlines() if line and not line.startswith('#'))
    try:
        values['WEB_ORIGIN'] = input('Calendar Sync public HTTPS origin (no path): ').strip()
        values['KEYCLOAK_ISSUER'] = input('Keycloak issuer from realm discovery: ').strip()
        values['KEYCLOAK_CLIENT_ID'] = input('Keycloak confidential client ID: ').strip()
        if any(any(char in value for char in '\r\n\'"\\$`') for value in values.values()):
            raise ValueError()
        settings = WebSettings.from_env(values)
        values['WEB_ORIGIN'],values['KEYCLOAK_ISSUER'] = settings.origin,settings.issuer
        descriptor = os.open(target,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600)
        with os.fdopen(descriptor,'w') as stream:
            stream.write('\n'.join(key+'='+value for key,value in values.items())+'\n')
        print('Saved private .env.web with settings/file references only; no client secret or tokens')
        return 0
    except Exception:
        print('web_configuration_failed; verify HTTPS addresses and client ID')
        return 2


if __name__=='__main__':
    raise SystemExit(main())
