"""Only for disposable Docker volumes: root setup then unprivileged app start.

Never use --initialize against real deployment paths; this uses a fake secret.
"""

import asyncio
import os
import sys
from unittest.mock import patch

import httpx

from calendar_sync.web import create_app
from calendar_sync.web_oidc import WebSettings
from calendar_sync.web_setup import initialize


async def check():
    settings = WebSettings.from_env(os.environ)
    assert os.geteuid()==10001, 'run runtime check as the image user'
    app = create_app(settings)  # Real Keycloak client; no fixture authentication mode.
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=settings.origin) as client:
            with patch('requests.sessions.Session.send',side_effect=AssertionError('network forbidden')):
                assert (await client.get('/')).status_code==200
                assert (await client.get('/auth/login')).status_code==200
                assert (await client.get('/api/v1/me')).status_code==401
                assert (await client.get('/healthz')).json()['status']=='alive'
    print('PASS: UID10001 reads initialized secrets, opens private stores and serves unauthenticated shell without network')


if __name__=='__main__':
    if sys.argv[1:]==['--initialize']:
        assert os.geteuid()==0, 'run initializer as setup user'
        initialize(WebSettings.from_env(os.environ),'SYNTHETIC_DEPLOYMENT_SECRET_NOT_REAL',uid=10001)
        print('PASS: disposable named volumes initialized privately for UID10001')
    elif not sys.argv[1:]:
        asyncio.run(check())
    else:
        raise SystemExit('Unsupported disposable test arguments')
