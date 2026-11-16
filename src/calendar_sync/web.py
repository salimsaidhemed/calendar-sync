"""Single-process FastAPI login shell; no provider calendar/engine operations."""

from contextlib import asynccontextmanager, contextmanager
import hmac
import logging
import os
import secrets
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from calendar_sync.control import ControlError, ControlStore, OwnerContext
from calendar_sync.web_oidc import KeycloakClient, WebError, WebSettings
from calendar_sync.web_sessions import Sessions
from calendar_sync.web_views import CSS, dashboard, error_page, login


COOKIE = '__Host-calendar_sync'


class LoginService:
    def __init__(self,settings,provider,sessions,clock):
        self.settings,self.provider,self.sessions,self.clock = settings,provider,sessions,clock

    @contextmanager
    def control(self):
        try:
            with ControlStore(self.settings.control_file,self.settings.control_key_file) as store:
                yield store
        except ControlError as error:
            if str(error)=='control_not_found':
                raise WebError('web_not_found',404) from None
            if str(error)=='control_owner_unavailable':
                raise WebError('web_owner_unavailable',403) from None
            raise WebError('web_control_unavailable',503) from None

    def prelogin(self,existing=None):
        with self.sessions.lock:
            previous = self.sessions.get(existing)
            if previous and previous.get('kind')=='auth':
                return existing,self.authenticated(existing)
            token = secrets.token_urlsafe(32)
            value = dict(kind='pre',csrf=secrets.token_urlsafe(32),expires=float(self.clock()+300))
            self.sessions.put(token,value)
            return token,value

    def csrf(self,token,provided,kind):
        value = self.sessions.get(token)
        if not value or value.get('kind')!=kind or not isinstance(provided,str) or not hmac.compare_digest(value['csrf'],provided):
            raise WebError('web_csrf_invalid',403)
        return value

    def start(self,token,csrf):
        with self.sessions.lock:
            value = self.csrf(token,csrf,'pre')
            if 'state' in value:
                raise WebError('web_login_already_started',409)
            value.update(state=secrets.token_urlsafe(32),nonce=secrets.token_urlsafe(32),verifier=secrets.token_urlsafe(48))
            url = self.provider.authorization_url(value['state'],value['nonce'],value['verifier'])
            self.sessions.put(token,value)
            return url

    @staticmethod
    def roles(claims,client_id):
        try:
            roles = claims.get('resource_access',{}).get(client_id,{}).get('roles',[])
        except (AttributeError,TypeError):
            raise WebError('web_role_required',403) from None
        if not isinstance(roles,list) or 'sync-user' not in roles:
            raise WebError('web_role_required',403)
        return ['sync-user']

    def callback(self,token,query):
        with self.sessions.lock:
            value = self.sessions.get(token)
            state = query.get('state','')
            if not value or value.get('kind')!='pre' or not isinstance(state,str) or not hmac.compare_digest(value.get('state',''),state) or not state:
                raise WebError('web_login_state_invalid')
            self.sessions.delete(token)  # Consume before exchanging the code, including failures.
            if query.get('iss',self.settings.issuer)!=self.settings.issuer or query.get('error') or not query.get('code'):
                raise WebError('web_authorization_failed')
            tokens,claims = self.provider.exchange(query['code'],value['verifier'],value['nonce'])
            roles = self.roles(claims,self.settings.client_id)
            self.provider.active(tokens,claims['sub'],claims['sid'])
            context = OwnerContext(self.settings.issuer,claims['sub'])
            with self.control() as store:
                owner = store.register_owner(context)
                store._owner(context)  # Existing disabled owners are not auto-enabled.
            now = self.clock()
            authenticated = dict(kind='auth',issuer=self.settings.issuer,sub=claims['sub'],sid=claims['sid'],
                owner_id=owner,nonce=value['nonce'],display=str(claims.get('preferred_username',claims['sub']))[:200],roles=roles,
                created=now,absolute=now+28800,expires=float(now+1800),csrf=secrets.token_urlsafe(32),tokens=tokens)
            new_token = secrets.token_urlsafe(32)
            self.sessions.put(new_token,authenticated)
            return new_token,authenticated

    def authenticated(self,token):
        with self.sessions.lock:
            value = self.sessions.get(token)
            if not value or value.get('kind')!='auth' or value['absolute']<=self.clock():
                self.sessions.delete(token)
                raise WebError('web_authentication_required')
            try:
                if value['tokens']['expires_at']<=self.clock()+30:
                    tokens,claims = self.provider.refresh(value['tokens'],nonce=value.get('nonce'))
                    if claims['sub']!=value['sub'] or claims['sid']!=value['sid']:
                        raise WebError('web_identity_invalid')
                    value['roles'] = self.roles(claims,self.settings.client_id)
                    value['tokens'] = tokens
                    self.sessions.put(token,value)  # Keep rotated refresh grant even if introspection then fails.
                self.provider.active(value['tokens'],value['sub'],value['sid'])
                context = OwnerContext(value['issuer'],value['sub'])
                with self.control() as store:
                    store._owner(context)
            except WebError as error:
                if error.status!=503:
                    self.sessions.delete(token)
                raise
            value['expires'] = float(min(value['absolute'],self.clock()+1800))
            self.sessions.put(token,value)
            return value

    def records(self,value,kind,identifier=None):
        context = OwnerContext(value['issuer'],value['sub'])
        with self.control() as store:
            if kind=='connections':
                if identifier is not None:
                    found = store._connection(store._owner(context),identifier)
                    return {key:found[key] for key in ('id','provider','account_id','status','revision')}
                return [dict(row) for row in store.connection.execute('SELECT id,provider,account_id,status,revision '
                    'FROM connections WHERE owner_id=? ORDER BY id LIMIT 101',(store._owner(context),))]
            if identifier is not None:
                return store.get_pair(context,identifier)
            return [dict(row) for row in store.connection.execute(
                'SELECT id,revision,state,dry_run,direction,legacy_review FROM pairs WHERE owner_id=? ORDER BY id LIMIT 101',
                (store._owner(context),))]


def create_app(settings=None,*,provider=None,clock=time.time):
    settings = settings or WebSettings.from_env(os.environ)
    sessions = Sessions(settings.session_file,settings.session_key_file,clock=clock)
    try:
        service = LoginService(settings,provider or KeycloakClient(settings,clock=clock),sessions,clock)
        with service.control():
            pass
    except BaseException:
        sessions.close()
        raise

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            sessions.close()

    app = FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.login = service

    def cookie(response,token,expires):
        response.set_cookie(COOKIE,token,max_age=max(1,int(expires-clock())),secure=True,httponly=True,samesite='lax',path='/')
        return response

    async def form(request,origin=True):
        if origin and request.headers.get('origin')!=settings.origin:
            raise WebError('web_origin_invalid',403)
        if request.headers.get('content-type','').split(';')[0]!='application/x-www-form-urlencoded':
            raise WebError('web_form_required',415)
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body)>20000:
                raise WebError('web_request_too_large',413)
        try:
            values = parse_qs(body.decode('utf-8'),keep_blank_values=True,max_num_fields=8)
        except (ValueError,UnicodeError):
            raise WebError('web_form_invalid',400) from None
        if any(len(items)!=1 for items in values.values()):
            raise WebError('web_form_invalid',400)
        return {key:items[0] for key,items in values.items()}

    @app.middleware('http')
    async def security(request,call_next):
        try:
            if request.headers.get('host','').lower()!=urlsplit(settings.origin).netloc.lower():
                raise WebError('web_host_invalid',400)
            if len(request.scope.get('query_string',b''))>20000:
                raise WebError('web_request_too_large',413)
            response = await call_next(request)
            if response.status_code<400 and hasattr(request.state,'auth_expires'):
                cookie(response,request.cookies[COOKIE],request.state.auth_expires)
        except WebError as error:
            browser = 'text/html' in request.headers.get('accept','') and request.url.path in ('/app','/auth/login','/auth/callback')
            if browser and request.url.path=='/app' and error.status==401:
                response = RedirectResponse('/auth/login',status_code=303)
            elif browser:
                response = HTMLResponse(error_page(error.status),status_code=error.status)
            else:
                response = JSONResponse({'code':str(error),'request_id':uuid4().hex},status_code=error.status)
            if error.status==401 and (request.url.path=='/app' or request.url.path.startswith('/api/')):
                response.delete_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='lax')
        except Exception:
            response = JSONResponse({'code':'web_request_failed','request_id':uuid4().hex},status_code=503)
        response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
            'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            'Strict-Transport-Security':'max-age=31536000',
            'Permissions-Policy':'camera=(), microphone=(), geolocation=()'})
        return response

    async def identity(request):
        value = await run_in_threadpool(service.authenticated,request.cookies.get(COOKIE))
        request.state.auth_expires = value['expires']
        return value

    @app.get('/healthz')
    async def health():
        return {'status':'alive','component':'web'}

    @app.get('/assets/app.css')
    async def style():
        return Response(CSS,media_type='text/css')

    @app.get('/')
    async def home():
        return HTMLResponse(login())

    @app.get('/auth/login')
    async def login_form(request:Request):
        token,value = await run_in_threadpool(service.prelogin,request.cookies.get(COOKIE))
        response = RedirectResponse('/app',status_code=303) if value['kind']=='auth' else HTMLResponse(login(value['csrf']))
        return cookie(response,token,value['expires'])

    @app.post('/auth/login')
    async def start_login(request:Request):
        values = await form(request)
        if set(values)!={'csrf'}:
            raise WebError('web_form_invalid',400)
        url = await run_in_threadpool(service.start,request.cookies.get(COOKIE),values['csrf'])
        return RedirectResponse(url,status_code=303)

    @app.get('/auth/callback')
    async def callback(request:Request):
        pairs = list(request.query_params.multi_items())
        if len({key for key,value in pairs})!=len(pairs):
            raise WebError('web_login_state_invalid')
        token,value = await run_in_threadpool(service.callback,request.cookies.get(COOKIE),dict(pairs))
        return cookie(RedirectResponse('/app',status_code=303),token,value['expires'])

    @app.get('/app')
    async def overview(request:Request):
        value = await identity(request)
        connections = await run_in_threadpool(service.records,value,'connections')
        pairs = await run_in_threadpool(service.records,value,'pairs')
        return cookie(HTMLResponse(dashboard(value['display'],value['csrf'],connections[:100],pairs[:100])),
                      request.cookies[COOKIE],value['expires'])

    @app.get('/api/v1/me')
    async def me(request:Request):
        value = await identity(request)
        return {'owner_id':value['owner_id'],'display_name':value['display'],'roles':value['roles'],
            'capabilities':{'login':True,'provider_connection':False,'pair_creation':False,'two_way':False}}

    @app.get('/api/v1/connections')
    async def connections(request:Request):
        items = await run_in_threadpool(service.records,await identity(request),'connections')
        return {'items':items[:100],'truncated':len(items)>100}

    @app.get('/api/v1/connections/{identifier}')
    async def connection(identifier:str,request:Request):
        return await run_in_threadpool(service.records,await identity(request),'connections',identifier)

    @app.get('/api/v1/pairs')
    async def pairs(request:Request):
        items = await run_in_threadpool(service.records,await identity(request),'pairs')
        return {'items':items[:100],'truncated':len(items)>100}

    @app.get('/api/v1/pairs/{identifier}')
    async def pair(identifier:str,request:Request):
        return await run_in_threadpool(service.records,await identity(request),'pairs',identifier)

    @app.post('/auth/logout')
    async def logout(request:Request):
        values = await form(request)
        if set(values)!={'csrf'}:
            raise WebError('web_form_invalid',400)
        def invalidate():
            with sessions.lock:
                service.csrf(request.cookies.get(COOKIE),values.get('csrf'),'auth')
                sessions.delete(request.cookies.get(COOKIE))
        await run_in_threadpool(invalidate)
        response = RedirectResponse('/',status_code=303)
        response.delete_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='lax')
        return response

    @app.post('/auth/backchannel-logout')
    async def backchannel(request:Request):
        values = await form(request,origin=False)
        if set(values)!={'logout_token'}:
            raise WebError('web_form_invalid',400)
        def revoke():
            with sessions.lock:
                claims = service.provider.claims(values['logout_token'],logout=True)
                sessions.backchannel(claims)
        await run_in_threadpool(revoke)
        return Response(status_code=204)

    return app


def main():
    import http.client
    import uvicorn
    for name in ('authlib','urllib3','requests','httpx','httpcore'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    http.client.HTTPConnection.debuglevel = 0
    try:
        app = create_app()
        uvicorn.run(app,host='0.0.0.0',port=8000,workers=1,proxy_headers=False,access_log=False,log_level='warning')
    except Exception:
        print('web_startup_failed')
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
