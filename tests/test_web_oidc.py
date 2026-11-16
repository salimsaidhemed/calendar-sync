from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from calendar_sync.web_oidc import KeycloakClient, WebError, WebSettings
from scripts.web_fixture import FakeKeycloak, settings_at


class OIDCTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.now = 2000000000.0
        self.settings = settings_at(self.root)
        self.fake = FakeKeycloak(self.settings,lambda:self.now)
        self.provider = self.fake.client()

    def test_signed_identity_enforces_issuer_audience_nonce_expiry_nbf_iat_and_authorized_party(self):
        self.assertEqual(self.provider.claims(self.fake.encode(),nonce='nonce')['sub'],'alice')
        for changes in ({'iss':'https://attacker.invalid'}, {'aud':'other'}, {'nonce':'wrong'},
                        {'exp':self.now-60}, {'nbf':self.now+60}, {'iat':self.now+60},
                        {'azp':'other'}, {'sub':''}, {'sid':''}, {'exp':float('nan')}):
            with self.subTest(changes=changes),self.assertRaisesRegex(WebError,'web_identity_invalid'):
                self.provider.claims(self.fake.encode(self.fake.claims(**changes)),nonce='nonce')

    def test_multiple_audiences_require_matching_azp_and_missing_essential_claims_fail(self):
        for name in ('iss','aud','iat','exp','sub','sid'):
            claims = self.fake.claims()
            del claims[name]
            with self.subTest(name=name),self.assertRaises(WebError):
                self.provider.claims(self.fake.encode(claims),nonce='nonce')
        claims = self.fake.claims(aud=['calendar-sync','other'])
        del claims['azp']
        with self.assertRaises(WebError):
            self.provider.claims(self.fake.encode(claims))
        claims['azp']='calendar-sync'
        self.assertEqual(self.provider.claims(self.fake.encode(claims))['sub'],'alice')

    def test_unknown_signing_key_is_refused_and_key_refresh_is_bounded(self):
        self.provider.claims(self.fake.encode())
        foreign = FakeKeycloak(self.settings,lambda:self.now)
        before = len(self.fake.records)
        for _ in range(3):
            with self.assertRaises(WebError):
                self.provider.claims(foreign.encode())
        self.assertEqual(len(self.fake.records),before)
        self.now+=31
        with self.assertRaises(WebError):
            self.provider.claims(foreign.encode())
        self.assertEqual(len(self.fake.records),before+1)
        self.fake.key = foreign.key
        self.now+=31
        self.assertEqual(self.provider.claims(foreign.encode())['sub'],'alice')
        self.assertEqual(len(self.fake.records),before+2)

    def test_signature_algorithm_and_embedded_key_urls_cannot_change_trusted_key_source(self):
        encoded = self.fake.encode()
        with self.assertRaises(WebError):
            self.provider.claims(encoded[:-8]+'AAAAAAAA')
        from joserfc import jwt
        from joserfc.jwk import OctKey
        symmetric = jwt.encode({'alg':'HS256'},self.fake.claims(),OctKey.generate_key())
        with self.assertRaises(WebError):
            self.provider.claims(symmetric)
        for header in ({'alg':'RS256','kid':self.fake.key.kid,'jku':'https://attacker.invalid/keys'},
                       {'alg':'RS256','kid':self.fake.key.kid,'x5u':'https://attacker.invalid/cert'}):
            with self.assertRaises(WebError):
                self.provider.claims(jwt.encode(header,self.fake.claims(),self.fake.key))
        self.assertTrue(all(record[0].url==self.settings.protocol+'certs' for record in self.fake.records))

    def test_mixed_encryption_and_signing_jwks_accepts_only_rsa_signing_key(self):
        public = self.fake.key.as_dict()
        self.fake.jwks = {'keys':[dict(public,alg='RSA-OAEP',use='enc',kid='encryption'),
                                  dict(public,alg='RS256',use='sig')]}
        self.assertEqual(self.provider.claims(self.fake.encode())['sub'],'alice')

    def test_pkce_exchange_uses_fixed_endpoints_basic_client_auth_and_safe_transport(self):
        state = self.fake.authorization(self.provider.authorization_url('state','nonce','v'*64))
        self.assertEqual(state,'state')
        tokens,claims = self.provider.exchange('code','v'*64,'nonce')
        self.provider.active(tokens,claims['sub'],claims['sid'])
        for request,trust_env,options in self.fake.records:
            self.assertTrue(request.url.startswith(self.settings.protocol))
            self.assertFalse(trust_env)
            self.assertFalse(options['allow_redirects'])
            self.assertEqual(options['timeout'],(5,10))
            self.assertIs(options['verify'],True)
        token = next(item[0] for item in self.fake.records if item[0].url.endswith('/token'))
        self.assertTrue(token.headers['Authorization'].startswith('Basic '))
        self.assertNotIn('SYNTHETIC_CLIENT_SECRET',token.body)
        # If the issuer supplies at_hash, the returned access token must match it.
        self.fake.claim_overrides={'at_hash':'wrong-hash'}
        with self.assertRaises(WebError):
            self.provider.exchange('second-code','v'*64,'nonce')

    def test_identity_outage_redirect_and_oversize_responses_fail_without_private_diagnostics(self):
        for status,large,offline in ((302,False,False),(200,True,False),(200,False,True)):
            self.fake.status,self.fake.large_body,self.fake.offline = status,large,offline
            provider = self.fake.client()
            with self.assertRaisesRegex(WebError,'^web_identity_unavailable$') as error:
                provider.claims(self.fake.encode())
            self.assertNotIn('SYNTHETIC',str(error.exception))

    def test_logout_tokens_validate_event_audience_age_nonce_and_session_identity(self):
        claims = self.fake.claims(events={'http://schemas.openid.net/event/backchannel-logout':{}},jti='logout-jti')
        del claims['nonce']
        self.assertEqual(self.provider.claims(self.fake.encode(claims),logout=True)['jti'],'logout-jti')
        multiple = dict(claims,aud=['calendar-sync','other-client'])
        del multiple['azp']
        self.assertEqual(self.provider.claims(self.fake.encode(multiple),logout=True)['jti'],'logout-jti')
        for changes in ({'events':{}},{'nonce':'forbidden'},{'iat':self.now-301},{'jti':''},{'aud':'other'}):
            with self.subTest(changes=changes),self.assertRaises(WebError):
                self.provider.claims(self.fake.encode(dict(claims,**changes)),logout=True)

    def test_web_environment_requires_https_unique_absolute_paths_and_private_client_secret(self):
        env = dict(WEB_ORIGIN=self.settings.origin,KEYCLOAK_ISSUER=self.settings.issuer,
            KEYCLOAK_CLIENT_ID=self.settings.client_id,KEYCLOAK_CLIENT_SECRET_FILE=self.settings.client_secret_file,
            WEB_CONTROL_FILE=self.settings.control_file,WEB_CONTROL_KEY_FILE=self.settings.control_key_file,
            WEB_SESSION_FILE=self.settings.session_file,WEB_SESSION_KEY_FILE=self.settings.session_key_file)
        self.assertEqual(WebSettings.from_env(env),self.settings)
        self.assertEqual(WebSettings.from_env(dict(env,WEB_ORIGIN='https://CALENDAR.example.invalid:443/')).origin,
                         self.settings.origin)
        for changes in ({'WEB_ORIGIN':'http://localhost:8000'},{'KEYCLOAK_ISSUER':'https://user:pass@example.invalid'},
                        {'WEB_ORIGIN':'https://example.invalid/path'},{'WEB_SESSION_FILE':self.settings.control_file},
                        {'WEB_SESSION_KEY_FILE':'relative'},{'KEYCLOAK_CLIENT_ID':'$PRIVATE_ENV'},
                        {'WEB_ORIGIN':'https://example.invalid?'}):
            with self.subTest(changes=changes),self.assertRaises(WebError):
                WebSettings.from_env(dict(env,**changes))
        Path(self.settings.client_secret_file).chmod(0o644)
        with self.assertRaisesRegex(WebError,'web_client_secret_unavailable'):
            KeycloakClient(self.settings)
