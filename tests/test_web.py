from contextlib import closing
import asyncio
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

import httpx

from calendar_sync.control import ControlStore, OwnerContext
from calendar_sync.web import COOKIE, create_app
from scripts.web_fixture import FakeKeycloak, settings_at


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.now = 2000000000.0
        self.settings = settings_at(self.root)
        self.fake = FakeKeycloak(self.settings,lambda:self.now)
        self.app = create_app(self.settings,provider=self.fake.client(),clock=lambda:self.now)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.addAsyncCleanup(self.lifespan.__aexit__,None,None,None)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url=self.settings.origin)
        self.addAsyncCleanup(self.client.aclose)

    async def start(self,client=None):
        client = client or self.client
        response = await client.get('/auth/login')
        csrf = re.search(r'name="csrf" value="([^"]+)"',response.text)[1]
        started = await client.post('/auth/login',data={'csrf':csrf},headers={'Origin':self.settings.origin})
        self.assertEqual(started.status_code,303,started.text)
        return self.fake.authorization(started.headers['location'])

    async def login(self,client=None):
        client = client or self.client
        state = await self.start(client)
        response = await client.get('/auth/callback',params={'state':state,'code':'fixture-code','iss':self.settings.issuer})
        self.assertEqual(response.status_code,303,response.text)
        return response

    def control(self):
        return ControlStore(self.settings.control_file,self.settings.control_key_file)

    async def test_public_page_has_security_headers_no_implicit_oauth_and_no_mutation_capabilities(self):
        page = await self.client.get('/')
        self.assertEqual(page.status_code,200)
        self.assertIn('Sign in',page.text)
        self.assertEqual(page.headers['cache-control'],'no-store')
        self.assertIn("frame-ancestors 'none'",page.headers['content-security-policy'])
        self.assertEqual(page.headers['referrer-policy'],'no-referrer')
        self.assertEqual(self.fake.records,[])
        self.assertEqual((await self.client.get('/docs')).status_code,404)

    async def test_prelogin_cookie_is_opaque_secure_host_only_and_does_not_authenticate(self):
        response = await self.client.get('/auth/login')
        cookie = response.headers['set-cookie']
        for item in ('Secure','HttpOnly','SameSite=lax','Path=/'):
            self.assertIn(item,cookie)
        self.assertNotIn('Domain=',cookie)
        token = self.client.cookies.get(COOKIE)
        self.assertEqual(len(token),43)
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,401)
        self.assertEqual(self.fake.records,[])

    async def test_login_requires_same_origin_csrf_single_fields_and_bounded_body(self):
        await self.client.get('/auth/login')
        for headers,data in (({}, {'csrf':'wrong'}),({'Origin':'https://attacker.invalid'},{'csrf':'wrong'}),
                             ({'Origin':self.settings.origin},{'csrf':'wrong'})):
            response = await self.client.post('/auth/login',headers=headers,data=data)
            self.assertEqual(response.status_code,403)
        duplicate = await self.client.post('/auth/login',content='csrf=a&csrf=b',
            headers={'Origin':self.settings.origin,'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual(duplicate.status_code,400)
        huge = await self.client.post('/auth/login',content='csrf='+'x'*20001,
            headers={'Origin':self.settings.origin,'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual(huge.status_code,413)
        self.assertEqual(self.fake.records,[])

    async def test_callback_rotates_session_registers_verified_owner_and_encrypts_tokens_at_rest(self):
        state = await self.start()
        old = self.client.cookies.get(COOKIE)
        response = await self.client.get('/auth/callback',params={'state':state,'code':'fixture'})
        self.assertEqual(response.status_code,303,response.text)
        token = self.client.cookies.get(COOKIE)
        self.assertNotEqual(old,token)
        self.assertIsNone(self.app.state.login.sessions.get(old))
        me = await self.client.get('/api/v1/me')
        self.assertEqual(me.status_code,200,me.text)
        self.assertIn('set-cookie',me.headers)
        self.assertFalse(me.json()['capabilities']['two_way'])
        self.assertNotIn('token',me.text)
        # Visiting sign-in again must not overwrite an authenticated browser cookie.
        self.assertEqual((await self.client.get('/auth/login')).status_code,303)
        self.assertEqual(self.client.cookies.get(COOKIE),token)
        with self.control() as store:
            self.assertEqual(store._owner(OwnerContext(self.settings.issuer,'alice')),me.json()['owner_id'])
        data = Path(self.settings.session_file).read_bytes()
        for secret in (token,b'SYNTHETIC_ACCESS',b'SYNTHETIC_REFRESH'):
            self.assertNotIn(secret.encode() if isinstance(secret,str) else secret,data)

    async def test_state_mismatch_cross_browser_and_replay_do_not_exchange_codes(self):
        state = await self.start()
        for supplied in ('wrong',''):
            response = await self.client.get('/auth/callback',params={'state':supplied,'code':'fixture'})
            self.assertEqual(response.status_code,401)
        self.assertEqual(self.fake.records,[])
        # Failure clears cookie: recover with an explicit fresh login, not replay.
        state = await self.start()
        old = self.client.cookies.get(COOKIE)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url=self.settings.origin) as other:
            self.assertEqual((await other.get('/auth/callback',params={'state':state,'code':'fixture'})).status_code,401)
        response = await self.client.get('/auth/callback',params={'state':state,'code':'fixture'})
        self.assertEqual(response.status_code,303)
        count = len(self.fake.records)
        self.client.cookies.set(COOKIE,old,domain='calendar.example.invalid',path='/')
        self.assertEqual((await self.client.get('/auth/callback',params={'state':state,'code':'fixture'})).status_code,401)
        self.assertEqual(len(self.fake.records),count)

    async def test_expired_and_duplicate_callback_parameters_are_rejected_before_network(self):
        state = await self.start()
        response = await self.client.get('/auth/callback?state='+state+'&state=second&code=fixture')
        self.assertEqual(response.status_code,401)
        state = await self.start()
        self.now+=301
        self.assertEqual((await self.client.get('/auth/callback',params={'state':state,'code':'fixture'})).status_code,401)
        self.assertEqual(self.fake.records,[])

    async def test_cancelled_or_wrong_issuer_callback_consumes_flow_without_owner_creation(self):
        for extra in ({'error':'access_denied'},{'code':'fixture','iss':'https://attacker.invalid'}):
            state = await self.start()
            response = await self.client.get('/auth/callback',params=dict(state=state,**extra))
            self.assertEqual(response.status_code,401)
        with self.control() as store:
            self.assertEqual(store.connection.execute('SELECT count(*) FROM owners').fetchone()[0],0)
        self.assertEqual(self.fake.records,[])

    async def test_required_client_role_is_checked_before_owner_provisioning(self):
        self.fake.roles=[]
        state = await self.start()
        response = await self.client.get('/auth/callback',params={'state':state,'code':'fixture'})
        self.assertEqual(response.status_code,403,response.text)
        with self.control() as store:
            self.assertEqual(store.connection.execute('SELECT count(*) FROM owners').fetchone()[0],0)

    async def test_idle_and_absolute_expiry_cannot_be_extended_by_an_old_cookie(self):
        await self.login()
        self.now+=1801
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,401)
        await self.login()
        token = self.client.cookies.get(COOKIE)
        sessions = self.app.state.login.sessions
        value = sessions.get(token)
        self.assertEqual(value['absolute']-value['created'],28800)
        value['absolute']=self.now+60
        sessions.put(token,value)
        self.now+=61
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,401)

    async def test_revocation_and_identity_outage_fail_closed_without_raw_diagnostics(self):
        await self.login()
        self.fake.offline=True
        response = await self.client.get('/api/v1/me')
        self.assertEqual(response.status_code,503,response.text)
        self.assertNotIn('SYNTHETIC',response.text)
        self.fake.offline=False
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,200)
        self.fake.revoked=True
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,401)

    async def test_concurrent_refresh_is_serialized_and_preserves_rotated_grant(self):
        await self.login()
        self.now+=280
        responses = await asyncio.gather(self.client.get('/api/v1/me'),self.client.get('/api/v1/me'))
        self.assertEqual([response.status_code for response in responses],[200,200])
        self.assertEqual(self.fake.refreshes,1)
        value = self.app.state.login.sessions.get(self.client.cookies.get(COOKIE))
        self.assertEqual(value['tokens']['refresh_token'],'SYNTHETIC_REFRESH_2')

    async def test_refresh_subject_change_or_lost_role_invalidates_local_session(self):
        await self.login()
        self.fake.subject='bob'
        self.now+=280
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,401)
        self.fake.subject='alice'
        await self.login()
        self.fake.roles=[]
        self.now+=280
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,403)
        self.fake.roles=['sync-user']
        await self.login()
        self.fake.claim_overrides={'nonce':'wrong-refresh-nonce'}
        self.now+=280
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,401)

    async def test_local_disabled_owner_blocks_session_even_when_keycloak_is_active(self):
        await self.login()
        with ControlStore(self.settings.control_file,self.settings.control_key_file,maintenance=True):
            self.assertEqual((await self.client.get('/api/v1/me')).status_code,503)
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,200)
        with self.control() as store:
            store.disable_owner(OwnerContext(self.settings.issuer,'alice'))
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,403)

    async def test_owner_scoped_records_and_foreign_ids_do_not_leak_credentials_or_other_users(self):
        await self.login()
        with self.control() as store:
            bob = OwnerContext(self.settings.issuer,'bob')
            store.register_owner(bob)
            google = store.create_connection(bob,'google','BOB_PRIVATE_ACCOUNT',b'BOB_REFRESH')
            nc = store.create_connection(bob,'nextcloud','bob',b'BOB_PASSWORD')
            pair = store.create_pair(bob,google,nc,('google:bob','nextcloud:bob'))
        for route in ('/api/v1/connections','/api/v1/pairs','/app'):
            response = await self.client.get(route)
            self.assertEqual(response.status_code,200)
            self.assertNotIn('BOB',response.text)
        for route in ('/api/v1/connections/','/api/v1/pairs/'):
            identifier = google if 'connections' in route else pair
            for value in (identifier,'unknown'):
                response = await self.client.get(route+value)
                self.assertEqual(response.status_code,404,response.text)
                self.assertEqual(response.json()['code'],'web_not_found')

    async def test_logout_requires_csrf_invalidates_session_and_does_not_touch_control_history(self):
        await self.login()
        response = await self.client.get('/app')
        csrf = re.search(r'name="csrf" value="([^"]+)"',response.text)[1]
        with closing(sqlite3.connect(self.settings.control_file)) as connection:
            before = '\n'.join(connection.iterdump())
        bad = await self.client.post('/auth/logout',data={'csrf':csrf},headers={'Origin':'https://attacker.invalid'})
        self.assertEqual(bad.status_code,403)
        token = self.client.cookies.get(COOKIE)
        result = await self.client.post('/auth/logout',data={'csrf':csrf},headers={'Origin':self.settings.origin})
        self.assertEqual(result.status_code,303)
        self.assertIsNone(self.app.state.login.sessions.get(token))
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,401)
        with closing(sqlite3.connect(self.settings.control_file)) as connection:
            self.assertEqual('\n'.join(connection.iterdump()),before)

    async def test_signed_backchannel_logout_revokes_only_matching_user_and_is_idempotent(self):
        await self.login()
        invalid = await self.client.post('/auth/backchannel-logout',data={'logout_token':'invalid'})
        self.assertEqual(invalid.status_code,401)
        self.assertNotIn('set-cookie',invalid.headers)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url=self.settings.origin) as bob:
            self.fake.subject='bob'
            await self.login(bob)
            claims = self.fake.claims(sub='alice',sid='sid-alice',jti='logout-1',
                events={'http://schemas.openid.net/event/backchannel-logout':{}})
            del claims['nonce']
            for _ in range(2):
                response = await self.client.post('/auth/backchannel-logout',data={'logout_token':self.fake.encode(claims)})
                self.assertEqual(response.status_code,204,response.text)
            self.assertEqual((await self.client.get('/api/v1/me')).status_code,401)
            self.assertEqual((await bob.get('/api/v1/me')).status_code,200)

    async def test_host_spoofing_and_forwarded_headers_cannot_change_redirects(self):
        bad = await self.client.get('/auth/login',headers={'Host':'attacker.invalid','X-Forwarded-Host':'calendar.example.invalid'})
        self.assertEqual(bad.status_code,400)
        state = await self.start()
        self.assertTrue(state)
        self.assertEqual(self.fake.records,[])

    async def test_dashboard_escapes_user_strings_and_unimplemented_mutations_are_unavailable(self):
        self.fake.display='<script>alert(1)</script>'
        await self.login()
        page = await self.client.get('/app')
        self.assertIn('&lt;script&gt;',page.text)
        self.assertNotIn('<script>',page.text)
        self.assertIn('not enabled',page.text)
        self.assertEqual((await self.client.post('/api/v1/pairs',json={'owner_id':'bob'})).status_code,405)

    async def test_sessions_survive_restart_but_second_web_process_cannot_open_store(self):
        await self.login()
        token = self.client.cookies.get(COOKIE)
        from calendar_sync.web_oidc import WebError
        with self.assertRaises(WebError):
            create_app(self.settings,provider=self.fake.client(),clock=lambda:self.now)
        self.app.state.login.sessions.close()
        replacement = create_app(self.settings,provider=self.fake.client(),clock=lambda:self.now)
        async with replacement.router.lifespan_context(replacement):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=replacement),base_url=self.settings.origin) as client:
                client.cookies.set(COOKIE,token,domain='calendar.example.invalid',path='/')
                self.assertEqual((await client.get('/api/v1/me')).status_code,200)

    async def test_modified_cookie_or_ciphertext_cannot_produce_an_authenticated_owner(self):
        await self.login()
        token = self.client.cookies.get(COOKIE)
        count = len(self.fake.records)
        changed = ('A' if token[0]!='A' else 'B')+token[1:]
        self.client.cookies.set(COOKIE,changed,domain='calendar.example.invalid',path='/')
        self.assertEqual((await self.client.get('/api/v1/me')).status_code,401)
        self.assertEqual(len(self.fake.records),count)
        self.client.cookies.set(COOKIE,token,domain='calendar.example.invalid',path='/')
        sessions = self.app.state.login.sessions
        sessions.connection.execute('UPDATE records SET envelope=?',(b'corrupt',))
        result = await self.client.get('/api/v1/me')
        self.assertEqual(result.status_code,503)
        self.assertNotIn('SYNTHETIC',result.text)

    async def test_web_setup_creates_private_files_without_overwrite_or_network(self):
        import os
        from dataclasses import replace
        from calendar_sync.control import ControlError
        from calendar_sync.web_setup import initialize
        root = self.root/'fresh'
        settings = replace(self.settings,control_file=str(root/'data'/'web'/'control'/'control.sqlite3'),
            session_file=str(root/'data'/'web'/'sessions'/'sessions.sqlite3'),
            control_key_file=str(root/'keys'/'control.key'),session_key_file=str(root/'keys'/'session.key'),
            client_secret_file=str(root/'keys'/'client-secret'))
        initialize(settings,'SYNTHETIC_NEW_SECRET',uid=os.geteuid())
        for path in (settings.control_key_file,settings.session_key_file,settings.client_secret_file):
            self.assertEqual(Path(path).stat().st_mode & 0o077,0)
        before = Path(settings.client_secret_file).read_bytes()
        with self.assertRaises(ControlError):
            initialize(settings,'OTHER',uid=os.geteuid())
        self.assertEqual(Path(settings.client_secret_file).read_bytes(),before)
        self.assertEqual(self.fake.records,[])

    async def test_unknown_session_schema_or_wrong_recovery_key_is_refused_without_reset(self):
        from dataclasses import replace
        from calendar_sync.control import create_key
        from calendar_sync.web_oidc import WebError
        self.app.state.login.sessions.close()
        with closing(sqlite3.connect(self.settings.session_file)) as connection, connection:
            connection.execute('PRAGMA user_version=999')
        before = Path(self.settings.session_file).read_bytes()
        with self.assertRaises(WebError):
            create_app(self.settings,provider=self.fake.client(),clock=lambda:self.now)
        self.assertEqual(Path(self.settings.session_file).read_bytes(),before)
        with closing(sqlite3.connect(self.settings.session_file)) as connection, connection:
            connection.execute('PRAGMA user_version=1')
        wrong = self.root/'keys'/'wrong-session.key'
        create_key(wrong)
        with self.assertRaises(WebError):
            create_app(replace(self.settings,session_key_file=str(wrong)),provider=self.fake.client(),clock=lambda:self.now)
