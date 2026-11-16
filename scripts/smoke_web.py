"""WEB-UAT-01–06: real ASGI routes and signed, offline OIDC fixture; no accounts."""

import asyncio
from pathlib import Path
import re
import tempfile

import httpx

from calendar_sync.control import ControlStore, OwnerContext
from calendar_sync.web import COOKIE, create_app
from web_fixture import FakeKeycloak, settings_at


async def exercise():
    with tempfile.TemporaryDirectory(prefix='calendar-web-uat-') as directory:
        root = Path(directory).resolve()
        now = [2000000000.0]
        settings = settings_at(root)
        fixture = FakeKeycloak(settings,lambda:now[0])
        app = create_app(settings,provider=fixture.client(),clock=lambda:now[0])
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=settings.origin) as client:
                async def signin():
                    page = await client.get('/auth/login')
                    assert page.status_code==200, 'login form unavailable'
                    csrf = re.search(r'name="csrf" value="([^"]+)"',page.text)[1]
                    started = await client.post('/auth/login',data={'csrf':csrf},headers={'Origin':settings.origin})
                    assert started.status_code==303, 'login start failed'
                    state = fixture.authorization(started.headers['location'])
                    result = await client.get('/auth/callback',params={'state':state,'code':'synthetic-code'})
                    assert result.status_code==303, 'signed callback failed'

                page = await client.get('/auth/login')
                assert all(item in page.headers['set-cookie'] for item in ('Secure','HttpOnly','SameSite=lax','Path=/'))
                rejected = await client.post('/auth/login',data={'csrf':'wrong'},headers={'Origin':'https://attacker.invalid'})
                assert rejected.status_code==403
                assert fixture.records==[]
                print('PASS WEB-UAT-01: secure prelogin cookie, CSRF/Origin denial and no implicit authorization')
                old = client.cookies.get(COOKIE)
                await signin()
                assert client.cookies.get(COOKIE)!=old
                me = await client.get('/api/v1/me')
                assert me.status_code==200 and me.json()['roles']==['sync-user']
                assert not me.json()['capabilities']['provider_connection']
                data = Path(settings.session_file).read_bytes()
                assert b'SYNTHETIC_ACCESS' not in data and b'SYNTHETIC_REFRESH' not in data
                print('PASS WEB-UAT-02: signed PKCE login, verified owner, session rotation and encrypted server tokens')
                with ControlStore(settings.control_file,settings.control_key_file) as store:
                    bob = OwnerContext(settings.issuer,'synthetic-bob')
                    store.register_owner(bob)
                    other = store.create_connection(bob,'google','SYNTHETIC_BOB_ACCOUNT',b'SYNTHETIC_BOB_SECRET')
                for identifier in (other,'unknown'):
                    response = await client.get('/api/v1/connections/'+identifier)
                    assert response.status_code==404 and response.json()['code']=='web_not_found'
                assert 'SYNTHETIC_BOB_ACCOUNT' not in (await client.get('/app')).text
                assert (await client.post('/api/v1/pairs',json={})).status_code==405
                print('PASS WEB-UAT-03: owner-scoped overview, generic foreign-ID denial and no calendar mutations')
                now[0]+=280
                assert (await client.get('/api/v1/me')).status_code==200 and fixture.refreshes==1
                fixture.offline=True
                outage = await client.get('/api/v1/me')
                assert outage.status_code==503 and 'SYNTHETIC_PRIVATE' not in outage.text
                fixture.offline=False
                assert (await client.get('/api/v1/me')).status_code==200
                print('PASS WEB-UAT-04: refresh and fail-closed identity outage with sanitized diagnostics')
                claims = fixture.claims(jti='synthetic-logout',events={'http://schemas.openid.net/event/backchannel-logout':{}})
                del claims['nonce']
                for _ in range(2):
                    response = await client.post('/auth/backchannel-logout',data={'logout_token':fixture.encode(claims)})
                    assert response.status_code==204
                assert (await client.get('/api/v1/me')).status_code==401
                print('PASS WEB-UAT-05: verified backchannel logout, replay safety and session revocation')
                await signin()
                page = await client.get('/app')
                csrf = re.search(r'name="csrf" value="([^"]+)"',page.text)[1]
                assert (await client.post('/auth/logout',data={'csrf':csrf},headers={'Origin':settings.origin})).status_code==303
                assert (await client.get('/api/v1/me')).status_code==401
                await signin()
                now[0]+=1801
                assert (await client.get('/api/v1/me')).status_code==401
                print('PASS WEB-UAT-06: local logout and idle expiry; no provider/engine operations')
    print('PASS: offline signed fixture only; synthetic files cleaned; real Keycloak acceptance remains Not run')


if __name__=='__main__':
    asyncio.run(exercise())
