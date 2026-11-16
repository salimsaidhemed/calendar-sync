"""Fixed-endpoint Keycloak OIDC client; no browser/provider calendar tokens."""

from dataclasses import dataclass
import hmac
import json
import math
import os
import re
from pathlib import Path
import time
from urllib.parse import urlsplit
from requests import RequestException

from authlib.integrations.requests_client import OAuth2Session
from authlib.oidc.core import CodeIDToken
from joserfc import jwt
from joserfc.jwk import KeySet

from calendar_sync.control import private_file


class WebError(RuntimeError):
    def __init__(self, code, status=401):
        super().__init__(code)
        self.status = status


def _https(value, *, origin=False):
    parsed = urlsplit(value)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or
            any(char in value for char in '?#$`\'"\\') or any(c.isspace() for c in value) or
            (origin and parsed.path not in ('','/'))):
        raise WebError('web_https_configuration_required',503)
    try:
        parsed.port
    except ValueError:
        raise WebError('web_invalid_configuration',503) from None
    if origin:
        host = '['+parsed.hostname+']' if ':' in parsed.hostname else parsed.hostname
        port = ':'+str(parsed.port) if parsed.port not in (None,443) else ''
        return 'https://'+host+port
    return value.rstrip('/')


@dataclass(frozen=True)
class WebSettings:
    origin: str
    issuer: str
    client_id: str
    client_secret_file: str
    control_file: str
    control_key_file: str
    session_file: str
    session_key_file: str
    ca_file: str | None = None

    @classmethod
    def from_env(cls, env):
        try:
            values = dict(origin=_https(env['WEB_ORIGIN'],origin=True),issuer=_https(env['KEYCLOAK_ISSUER']),
                client_id=env['KEYCLOAK_CLIENT_ID'],client_secret_file=env['KEYCLOAK_CLIENT_SECRET_FILE'],
                control_file=env['WEB_CONTROL_FILE'],control_key_file=env['WEB_CONTROL_KEY_FILE'],
                session_file=env['WEB_SESSION_FILE'],session_key_file=env['WEB_SESSION_KEY_FILE'],
                ca_file=env.get('WEB_OIDC_CA_FILE') or None)
            if re.fullmatch(r'[A-Za-z0-9._-]{1,128}',values['client_id']) is None:
                raise ValueError()
            for key in ('client_secret_file','control_file','control_key_file','session_file','session_key_file'):
                if not Path(values[key]).is_absolute():
                    raise ValueError()
            if len({str(Path(values[key]).resolve()) for key in (
                    'client_secret_file','control_file','control_key_file','session_file','session_key_file')})!=5:
                raise ValueError()
            if values['ca_file'] and not Path(values['ca_file']).is_absolute():
                raise ValueError()
            return cls(**values)
        except (KeyError,ValueError,TypeError):
            raise WebError('web_invalid_configuration',503) from None

    @property
    def callback(self):
        return self.origin+'/auth/callback'

    @property
    def protocol(self):
        return self.issuer+'/protocol/openid-connect/'


class BoundedOAuth2Session(OAuth2Session):
    """Authlib requests transport with bounded body/timeouts and no redirect/proxy."""
    def __init__(self,*args,ca=True,**kwargs):
        super().__init__(*args,**kwargs)
        self.trust_env = False
        self._ca = ca

    def request(self,method,url,**kwargs):
        kwargs.update(timeout=(5,10),allow_redirects=False,verify=self._ca,stream=True)
        response = super().request(method,url,**kwargs)
        try:
            if response.status_code not in (200,400,401):
                raise WebError('web_identity_unavailable',503)
            body = bytearray()
            for chunk in response.iter_content(8192):
                body.extend(chunk)
                if len(body)>262144:
                    raise WebError('web_identity_unavailable',503)
            response._content = bytes(body)
            response._content_consumed = True
            return response
        finally:
            response.close()


class KeycloakClient:
    def __init__(self,settings,*,clock=time.time,transport=BoundedOAuth2Session):
        self.settings = settings
        self.clock = clock
        self.transport = transport
        try:
            with os.fdopen(private_file(settings.client_secret_file),'r') as stream:
                self.secret = stream.read(4097).strip()
            if not self.secret or len(self.secret)>4096:
                raise ValueError()
        except Exception:
            raise WebError('web_client_secret_unavailable',503) from None
        self._keys = None
        self._key_time = float('-inf')

    def _http(self):
        return self.transport(self.settings.client_id,self.secret,scope='openid',
            redirect_uri=self.settings.callback,code_challenge_method='S256',
            token_endpoint_auth_method='client_secret_basic',ca=self.settings.ca_file or True)

    def authorization_url(self,state,nonce,verifier):
        with self._http() as client:
            url,_ = client.create_authorization_url(self.settings.protocol+'auth',state=state,
                nonce=nonce,code_verifier=verifier,response_mode='query')
            return url

    def _keyset(self):
        if self._keys is None and self.clock()-self._key_time<30:
            raise WebError('web_identity_unavailable',503)
        if self._keys is None or self.clock()-self._key_time>=300:
            self._key_time = self.clock()  # Back off failed and unknown-key requests too.
            try:
                with self._http() as client:
                    data = client.get(self.settings.protocol+'certs',withhold_token=True).json()
                if not isinstance(data.get('keys'),list) or not 1<=len(data['keys'])<=32:
                    raise ValueError()
                candidates = [key for key in data['keys'] if key.get('kty')=='RSA' and
                    key.get('alg','RS256')=='RS256' and key.get('use','sig')=='sig' and 'd' not in key]
                if not 1<=len(candidates)<=16:
                    raise ValueError()
                self._keys = KeySet.import_key_set({'keys':candidates})
            except Exception:
                self._keys = None
                raise WebError('web_identity_unavailable',503) from None
        return self._keys

    def claims(self,encoded,*,nonce=None,nonce_optional=False,logout=False):
        try:
            if not isinstance(encoded,str) or len(encoded)>16384:
                raise ValueError()
            keys = self._keyset()
            try:
                token = jwt.decode(encoded,keys,algorithms=['RS256'])
            except Exception:
                if self.clock()-self._key_time>=30:
                    self._keys = None
                    token = jwt.decode(encoded,self._keyset(),algorithms=['RS256'])
                else:
                    raise
            if any(name in token.header for name in ('jku','x5u','jwk')):
                raise ValueError()
            claims = token.claims
            requests = dict(iss={'essential':True,'value':self.settings.issuer},
                aud={'essential':True,'value':self.settings.client_id},iat={'essential':True},
                exp={'essential':not logout})
            jwt.JWTClaimsRegistry(now=int(self.clock()),leeway=30,**requests).validate(claims)
            for name in ('iat','exp','nbf'):
                if name in claims and (type(claims[name]) not in (int,float) or not math.isfinite(claims[name])):
                    raise ValueError()
            if claims['iat']>self.clock()+30:
                raise ValueError()
            if not logout and claims.get('azp',self.settings.client_id)!=self.settings.client_id:
                raise ValueError()
            if not logout and isinstance(claims['aud'],list) and len(claims['aud'])>1 and claims.get('azp')!=self.settings.client_id:
                raise ValueError()
            if logout:
                if ('nonce' in claims or not isinstance(claims.get('jti'),str) or not claims['jti'] or
                    self.clock()-claims['iat']>300 or
                    claims.get('events')!={'http://schemas.openid.net/event/backchannel-logout':{}} or
                    any(name in claims and (not isinstance(claims[name],str) or not claims[name]) for name in ('sid','sub')) or
                    not any(isinstance(claims.get(name),str) and claims[name] for name in ('sid','sub'))):
                    raise ValueError()
            else:
                if not isinstance(claims.get('sub'),str) or not claims['sub'] or not isinstance(claims.get('sid'),str) or not claims['sid']:
                    raise ValueError()
                if nonce is not None and (not nonce_optional or 'nonce' in claims) and not hmac.compare_digest(claims.get('nonce',''),nonce):
                    raise ValueError()
            return claims
        except WebError:
            raise
        except Exception:
            raise WebError('web_identity_invalid') from None

    def exchange(self,code,verifier,nonce):
        try:
            with self._http() as client:
                tokens = client.fetch_token(self.settings.protocol+'token',code=code,code_verifier=verifier)
            claims = self.claims(tokens['id_token'],nonce=nonce)
            self._access_hash(claims,tokens)
            return self._tokens(tokens),claims
        except WebError:
            raise
        except Exception:
            raise WebError('web_authorization_failed') from None

    def refresh(self,tokens,nonce=None):
        try:
            with self._http() as client:
                updated = client.refresh_token(self.settings.protocol+'token',refresh_token=tokens['refresh_token'])
            updated.setdefault('refresh_token',tokens['refresh_token'])
            claims = self.claims(updated['id_token'],nonce=nonce,nonce_optional=True)
            self._access_hash(claims,updated)
            return self._tokens(updated),claims
        except WebError:
            raise
        except RequestException:
            raise WebError('web_identity_unavailable',503) from None
        except Exception:
            raise WebError('web_reauthorization_required') from None

    @staticmethod
    def _access_hash(claims,tokens):
        if 'at_hash' in claims:
            if not isinstance(claims['at_hash'],str) or not claims['at_hash'] or not tokens.get('access_token'):
                raise WebError('web_identity_invalid')
            CodeIDToken(claims,{'alg':'RS256'},params={'access_token':tokens['access_token']}).validate_at_hash()

    def _tokens(self,tokens):
        if (not isinstance(tokens.get('access_token'),str) or not tokens['access_token'] or
                not isinstance(tokens.get('refresh_token'),str) or not tokens['refresh_token'] or
                tokens.get('token_type','').lower()!='bearer'):
            raise WebError('web_authorization_failed')
        if len(tokens['access_token'])>16384 or len(tokens['refresh_token'])>16384:
            raise WebError('web_authorization_failed')
        lifetime = tokens.get('expires_in')
        if type(lifetime) not in (int,float) or not 0<lifetime<=86400:
            raise WebError('web_authorization_failed')
        return dict(access_token=tokens['access_token'],refresh_token=tokens['refresh_token'],
                    expires_at=self.clock()+lifetime)

    def active(self,tokens,subject,sid):
        try:
            with self._http() as client:
                result = client.post(self.settings.protocol+'token/introspect',
                    data={'token':tokens['access_token'],'token_type_hint':'access_token'},
                    auth=(self.settings.client_id,self.secret),withhold_token=True).json()
            if result.get('active') is not True or result.get('client_id')!=self.settings.client_id or result.get('sub')!=subject or result.get('sid',sid)!=sid:
                raise WebError('web_reauthorization_required')
        except WebError:
            raise
        except Exception:
            raise WebError('web_identity_unavailable',503) from None
