"""Real local Keycloak protocol acceptance; never accepts customer URLs or secrets."""

from html.parser import HTMLParser
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urljoin, urlsplit

import requests

try:
    from examples.keycloak.fixture import ORIGIN, IDENTITY, ISSUER, REALM, Paths, verify
except ModuleNotFoundError:
    from fixture import ORIGIN, IDENTITY, ISSUER, REALM, Paths, verify


def local_url(url):
    parts = urlsplit(url)
    if (parts.scheme+'://'+parts.netloc not in (ORIGIN, IDENTITY)
            or parts.username or parts.password or parts.fragment):
        raise ValueError('local_test_url_refused')
    return url


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag=='form':
            self.current = {'action':values.get('action',''), 'id':values.get('id'), 'inputs':{}}
            self.forms.append(self.current)
        if tag=='input' and self.current is not None and values.get('name'):
            self.current['inputs'][values['name']] = values.get('value','')

    def handle_endtag(self, tag):
        if tag=='form':
            self.current = None


def session():
    value = requests.Session()
    value.trust_env = False
    value.verify = '/uat-tls/ca.crt'
    return value


def request(client, method, url, **kwargs):
    return client.request(method,local_url(url),allow_redirects=False,timeout=15,**kwargs)


def follow(client, response):
    for _ in range(10):
        if response.status_code not in (301,302,303,307,308):
            return response
        response = request(client,'GET',urljoin(response.url,response.headers['location']))
    raise ValueError('local_test_redirect_limit')


def login(name, credentials):
    client = session()
    page = request(client,'GET',ORIGIN+'/auth/login')
    csrf = Forms(page.text).forms[0]['inputs']['csrf']
    page = follow(client,request(client,'POST',ORIGIN+'/auth/login',
        data={'csrf':csrf},headers={'Origin':ORIGIN}))
    form = next(item for item in Forms(page.text).forms if item['id']=='kc-form-login')
    target = local_url(urljoin(page.url,form['action']))
    # A hostile form must never receive even disposable credentials.
    assert urlsplit(target).netloc==urlsplit(IDENTITY).netloc
    fields = form['inputs'] | {'username':name,'password':credentials['users'][name]['password']}
    return client,follow(client,request(client,'POST',target,data=fields))


def records(client,kind):
    response = request(client,'GET',ORIGIN+'/api/v1/'+kind)
    assert response.status_code==200
    return response.json()['items']


def has_session(client):
    token = client.cookies.get('__Host-calendar_sync')
    digest = hashlib.sha256(token.encode()).hexdigest()
    with sqlite3.connect('file:/data/web/sessions/sessions.sqlite3?mode=ro',uri=True) as connection:
        return connection.execute('SELECT 1 FROM records WHERE id=?',(digest,)).fetchone() is not None


def main():
    case = 'LOCAL-KC-01'
    try:
        assert os.environ.get('LOCAL_KEYCLOAK_FIXTURE')=='localhost-test-only'
        credentials = verify(Paths())
        discovery = request(session(),'GET',ISSUER+'/.well-known/openid-configuration')
        assert discovery.status_code==200 and discovery.json()['issuer']==ISSUER
        alice,page = login('alice',credentials)
        assert page.status_code==200 and 'alice' in page.text
        me = request(alice,'GET',ORIGIN+'/api/v1/me').json()
        assert me['roles']==['sync-user'] and not me['capabilities']['provider_connection']
        print('PASS LOCAL-KC-01: trusted localhost TLS and real code/PKCE login',flush=True)

        case = 'LOCAL-KC-02'
        bob,page = login('bob',credentials)
        assert page.status_code==200
        for kind in ('connections','pairs'):
            own,other = records(alice,kind),records(bob,kind)
            assert len(own)==len(other)==(2 if kind=='connections' else 1)
            assert {x['id'] for x in own}.isdisjoint(x['id'] for x in other)
            assert request(alice,'GET',ORIGIN+'/api/v1/'+kind+'/'+other[0]['id']).status_code==404
        assert all(x['state']=='paused' and x['dry_run'] for x in records(alice,'pairs'))
        assert request(alice,'POST',ORIGIN+'/api/v1/pairs',json={}).status_code==405
        print('PASS LOCAL-KC-02: separate owners, paused synthetic records and read-only APIs',flush=True)

        case = 'LOCAL-KC-03'
        denied,page = login('denied',credentials)
        assert page.status_code==403
        assert request(denied,'GET',ORIGIN+'/api/v1/me').status_code==401
        print('PASS LOCAL-KC-03: missing client role denied',flush=True)

        case = 'LOCAL-KC-04'
        time.sleep(20)  # 45-second realm access token enters application's 30-second refresh window.
        assert request(alice,'GET',ORIGIN+'/api/v1/me').status_code==200
        assert request(bob,'GET',ORIGIN+'/api/v1/me').status_code==200
        print('PASS LOCAL-KC-04: authenticated access through real token refresh window',flush=True)

        case = 'LOCAL-KC-05'
        admin = session()
        token = request(admin,'POST',IDENTITY+'/realms/master/protocol/openid-connect/token',data={
            'client_id':'admin-cli','grant_type':'password','username':'admin',
            'password':credentials['admin']['password']})
        assert token.status_code==200
        assert has_session(alice)
        revoked = request(admin,'POST',IDENTITY+'/admin/realms/'+REALM+'/users/'+
            credentials['users']['alice']['subject']+'/logout',
            headers={'Authorization':'Bearer '+token.json()['access_token']})
        assert revoked.status_code==204
        for _ in range(20):
            if not has_session(alice):
                break
            time.sleep(0.5)
        assert not has_session(alice)  # Observe backchannel deletion before any protected request.
        assert has_session(bob)
        assert request(alice,'GET',ORIGIN+'/api/v1/me').status_code==401
        assert request(bob,'GET',ORIGIN+'/api/v1/me').status_code==200
        print('PASS LOCAL-KC-05: real Keycloak backchannel logout; other owner stays signed in',flush=True)

        case = 'LOCAL-KC-06'
        page = request(bob,'GET',ORIGIN+'/app')
        form = next(item for item in Forms(page.text).forms if item['action']=='/auth/logout')
        assert request(bob,'POST',ORIGIN+'/auth/logout',data={'csrf':form['inputs']['csrf']},
            headers={'Origin':ORIGIN}).status_code==303
        assert request(bob,'GET',ORIGIN+'/api/v1/me').status_code==401
        print('PASS LOCAL-KC-06: CSRF-protected local logout; no calendar credentials or writes',flush=True)
        return 0
    except Exception as error:
        # Never print authorization URLs, HTML, token responses, cookies or passwords.
        print('FAIL '+case+': '+type(error).__name__+'; inspect local setup (no secrets printed)',flush=True)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
