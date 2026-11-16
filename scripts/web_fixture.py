"""Offline signed Keycloak fixture for tests only, never an app startup mode."""

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from joserfc import jwt
from joserfc.jwk import RSAKey

from calendar_sync.control import create_key
from calendar_sync.web_oidc import BoundedOAuth2Session, KeycloakClient, WebSettings


def settings_at(root):
    root = Path(root)
    control_key,session_key = root/'keys'/'control.key',root/'keys'/'session.key'
    create_key(control_key)
    create_key(session_key)
    secret = root/'keys'/'client-secret'
    secret.write_text('SYNTHETIC_CLIENT_SECRET')
    secret.chmod(0o600)
    return WebSettings('https://calendar.example.invalid','https://login.example.invalid/realms/fixture',
        'calendar-sync',str(secret),str(root/'control'/'control.sqlite3'),str(control_key),
        str(root/'sessions'/'sessions.sqlite3'),str(session_key))


class FakeKeycloak:
    def __init__(self,settings,clock,key=None):
        self.settings,self.clock = settings,clock
        self.key = key or RSAKey.generate_key(parameters={'kid':'fixture-key'})
        self.subject = 'alice'
        self.roles = ['sync-user']
        self.display = 'Alice'
        self.nonce = 'nonce'
        self.challenge = None
        self.records = []
        self.tokens = {}
        self.counter = 0
        self.refreshes = 0
        self.offline = False
        self.revoked = False
        self.status = 200
        self.large_body = False
        self.jwks = None
        self.claim_overrides = {}

    def claims(self,**overrides):
        data = dict(iss=self.settings.issuer,aud=self.settings.client_id,azp=self.settings.client_id,
            sub=self.subject,sid='sid-'+self.subject,nonce=self.nonce,iat=self.clock(),exp=self.clock()+300,
            preferred_username=self.display,resource_access={self.settings.client_id:{'roles':self.roles}})
        data.update(self.claim_overrides)
        data.update(overrides)
        return data

    def encode(self,claims=None,**kwargs):
        return jwt.encode({'alg':'RS256','kid':self.key.kid},claims or self.claims(),self.key,**kwargs)

    def transport(self,*args,**kwargs):
        session = BoundedOAuth2Session(*args,**kwargs)
        session.send = lambda request,**options:self.send(request,session,options)
        return session

    def client(self):
        return KeycloakClient(self.settings,clock=self.clock,transport=self.transport)

    def send(self,request,session,options):
        self.records.append((request,session.trust_env,options))
        if self.offline:
            raise requests.ConnectionError('SYNTHETIC_PRIVATE_EXCEPTION')
        endpoint = request.url.removeprefix(self.settings.protocol)
        fields = parse_qs(request.body or '')
        if endpoint=='certs':
            public = self.key.as_dict()
            public.update(alg='RS256',use='sig')
            data = self.jwks or {'keys':[public]}
        elif endpoint=='token':
            if fields.get('grant_type')==['authorization_code']:
                verifier = fields['code_verifier'][0]
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                assert challenge==self.challenge, 'fixture PKCE mismatch'
            else:
                self.refreshes+=1
                assert fields.get('refresh_token'), 'fixture missing refresh grant'
            self.counter+=1
            access = 'SYNTHETIC_ACCESS_'+str(self.counter)
            claims = self.claims()
            self.tokens[access]=claims
            data = dict(access_token=access,refresh_token='SYNTHETIC_REFRESH_'+str(self.counter),
                        token_type='Bearer',expires_in=300,id_token=self.encode(claims))
        elif endpoint=='token/introspect':
            claims = self.tokens.get(fields.get('token',[''])[0],{})
            data = dict(active=not self.revoked,client_id=self.settings.client_id,sub=claims.get('sub'),sid=claims.get('sid'))
        else:
            raise AssertionError('unexpected fixture network endpoint')
        response = requests.Response()
        response.status_code = self.status
        response.headers['Content-Type']='application/json'
        response._content = b'x'*262145 if self.large_body else json.dumps(data).encode()
        response._content_consumed = True
        response.url = request.url
        return response

    def authorization(self,url):
        query = parse_qs(urlsplit(url).query)
        self.nonce = query['nonce'][0]
        self.challenge = query['code_challenge'][0]
        return query['state'][0]
