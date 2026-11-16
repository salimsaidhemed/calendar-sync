"""Local-only disposable Keycloak fixture. Never used by production app startup."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from calendar_sync.control import ControlError, ControlStore, OwnerContext
from calendar_sync.web_oidc import WebSettings
from calendar_sync.web_setup import initialize as initialize_web


ORIGIN = 'https://localhost:8443'
IDENTITY = 'https://localhost:9443'
REALM = 'calendar-sync-uat'
ISSUER = IDENTITY+'/realms/'+REALM
CLIENT = 'calendar-sync-uat'


@dataclass(frozen=True)
class Paths:
    metadata: Path = Path('/fixture')
    imports: Path = Path('/keycloak-import')
    tls: Path = Path('/uat-tls')
    data: Path = Path('/data/web')
    keys: Path = Path('/run/web-secrets')

    @classmethod
    def under(cls,root):
        return cls(root/'fixture',root/'imports',root/'tls',root/'data'/'web',root/'keys')

    def settings(self):
        return WebSettings(ORIGIN,ISSUER,CLIENT,str(self.keys/'client-secret'),
            str(self.data/'control'/'control.sqlite3'),str(self.keys/'control.key'),
            str(self.data/'sessions'/'sessions.sqlite3'),str(self.keys/'session.key'),str(self.tls/'ca.crt'))

    def immutable(self):
        return {'credentials':self.metadata/'credentials.json','realm':self.imports/'realm.json',
            'admin':self.imports/'admin-password','subjects':self.tls/'subjects.json',
            'ca':self.tls/'ca.crt','certificate':self.tls/'server.crt','server_key':self.tls/'server.key',
            'proxy':self.tls/'Caddyfile','control_key':self.keys/'control.key',
            'session_key':self.keys/'session.key','client_secret':self.keys/'client-secret'}


def write(path,data,mode=0o600,uid=None):
    descriptor = os.open(path,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,mode)
    with os.fdopen(descriptor,'wb') as stream:
        stream.write(data.encode() if isinstance(data,str) else data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)
    if uid is not None:
        os.chown(path,uid,os.getegid() if uid==os.geteuid() else uid)


def realm_document(credentials):
    users = []
    for username,entry in credentials['users'].items():
        user = dict(id=entry['subject'],username=username,enabled=True,emailVerified=True,
            firstName=username.capitalize(),lastName='Local UAT',email=username+'@calendar-sync.invalid',
            requiredActions=[],credentials=[{'type':'password','value':entry['password'],'temporary':False}])
        if username!='denied':
            user['clientRoles']={CLIENT:['sync-user']}
        users.append(user)
    client = dict(clientId=CLIENT,enabled=True,protocol='openid-connect',publicClient=False,
        clientAuthenticatorType='client-secret',secret=credentials['client_secret'],
        standardFlowEnabled=True,directAccessGrantsEnabled=False,implicitFlowEnabled=False,
        serviceAccountsEnabled=False,consentRequired=False,frontchannelLogout=False,
        redirectUris=[ORIGIN+'/auth/callback'],webOrigins=[ORIGIN],
        attributes={'pkce.code.challenge.method':'S256','id.token.signed.response.alg':'RS256',
            'backchannel.logout.url':ORIGIN+'/auth/backchannel-logout',
            'backchannel.logout.session.required':'true'},
        protocolMappers=[dict(name='local-client-roles-in-id-token',protocol='openid-connect',
            protocolMapper='oidc-usermodel-client-role-mapper',config={
                'usermodel.clientRoleMapping.clientId':CLIENT,'claim.name':'resource_access.'+CLIENT+'.roles',
                'jsonType.label':'String','multivalued':'true','id.token.claim':'true',
                'access.token.claim':'true','userinfo.token.claim':'false'}),
            dict(name='local-api-audience',protocol='openid-connect',protocolMapper='oidc-audience-mapper',
                config={'included.client.audience':CLIENT,'id.token.claim':'false','access.token.claim':'true',
                        'introspection.token.claim':'true'}),
            dict(name='local-username',protocol='openid-connect',protocolMapper='oidc-usermodel-property-mapper',
                config={'user.attribute':'username','claim.name':'preferred_username','jsonType.label':'String',
                        'id.token.claim':'true','access.token.claim':'true','introspection.token.claim':'true'})])
    return dict(realm=REALM,enabled=True,sslRequired='all',registrationAllowed=False,
        resetPasswordAllowed=False,rememberMe=False,accessTokenLifespan=45,
        ssoSessionIdleTimeout=1800,ssoSessionMaxLifespan=28800,
        roles={'client':{CLIENT:[{'name':'sync-user'}]}},clients=[client],users=users)


def tls_material(fixture_id):
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Calendar Sync Local UAT '+fixture_id[:8])])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
        .not_valid_after(now+timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,
            data_encipherment=False,key_agreement=False,key_cert_sign=True,crl_sign=True,
            encipher_only=None,decipher_only=None),critical=True).sign(ca_key,hashes.SHA256()))
    leaf_key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]))
        .issuer_name(name).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
            x509.IPAddress(ipaddress.ip_address('127.0.0.1')),x509.IPAddress(ipaddress.ip_address('::1'))]),critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
        .sign(ca_key,hashes.SHA256()))
    # The CA signing key is never serialized or persisted.
    return (ca.public_bytes(serialization.Encoding.PEM),leaf.public_bytes(serialization.Encoding.PEM),
        leaf_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))


def verify(paths):
    marker = json.loads((paths.metadata/'complete.json').read_text())
    if marker.get('format')!=1 or marker.get('origin')!=ORIGIN or marker.get('issuer')!=ISSUER:
        raise ControlError('local_fixture_marker_invalid')
    for name,path in paths.immutable().items():
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=marker['files'].get(name):
            raise ControlError('local_fixture_integrity_failed')
    leaf = x509.load_pem_x509_certificate((paths.tls/'server.crt').read_bytes())
    if leaf.not_valid_after_utc<=datetime.now(timezone.utc):
        raise ControlError('local_fixture_certificate_expired')
    return json.loads((paths.metadata/'credentials.json').read_text())


def initialize(paths=Paths(),*,app_uid=10001,identity_uid=1000):
    if (paths.metadata/'complete.json').exists():
        verify(paths)
        return False
    directories = (paths.metadata,paths.imports,paths.tls,paths.data,paths.keys)
    for path in directories:
        if path.is_symlink() or (path.exists() and any(path.iterdir())):
            raise ControlError('local_fixture_partial_or_foreign_state')
    fixture_id = uuid4().hex
    credentials = dict(fixture_id=fixture_id,client_secret=secrets.token_urlsafe(40),
        admin={'username':'admin','password':secrets.token_urlsafe(32)},
        users={name:{'subject':str(uuid4()),'password':secrets.token_urlsafe(32)} for name in ('alice','bob','denied')})
    # The web initializer prepares only fresh application state, with its usual key guards.
    initialize_web(paths.settings(),credentials['client_secret'],uid=app_uid)
    for path,mode,uid in ((paths.metadata,0o700,os.geteuid()),(paths.imports,0o700,identity_uid),
                          (paths.tls,0o755,os.geteuid())):
        path.mkdir(parents=True,exist_ok=True)
        path.chmod(mode)
        os.chown(path,uid,os.getegid() if uid==os.geteuid() else uid)
    write(paths.metadata/'credentials.json',json.dumps(credentials))
    write(paths.imports/'realm.json',json.dumps(realm_document(credentials)),uid=identity_uid)
    write(paths.imports/'admin-password',credentials['admin']['password']+'\n',uid=identity_uid)
    ca,leaf,key = tls_material(fixture_id)
    write(paths.tls/'ca.crt',ca,0o644)
    write(paths.tls/'server.crt',leaf,0o644)
    write(paths.tls/'server.key',key)
    write(paths.tls/'subjects.json',json.dumps({name:entry['subject'] for name,entry in credentials['users'].items()}),0o644)
    write(paths.tls/'Caddyfile','''{
    admin off
    auto_https off
    persist_config off
}
https://localhost:8443 {
    tls /uat-tls/server.crt /uat-tls/server.key
    reverse_proxy 127.0.0.1:8000
}
https://localhost:9443 {
    tls /uat-tls/server.crt /uat-tls/server.key
    reverse_proxy 127.0.0.1:8080
}
''',0o644)
    hashes_by_name = {name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in paths.immutable().items()}
    write(paths.metadata/'complete.json',json.dumps(dict(format=1,fixture_id=fixture_id,
        origin=ORIGIN,issuer=ISSUER,files=hashes_by_name)))
    return True


def seed(paths=Paths()):
    subjects = json.loads((paths.tls/'subjects.json').read_text())
    settings = paths.settings()
    with ControlStore(settings.control_file,settings.control_key_file) as store:
        for username in ('alice','bob'):
            context = OwnerContext(ISSUER,subjects[username])
            owner = store.register_owner(context)
            engine_id = 'local-uat-'+username
            with store.transaction():
                old = store.connection.execute('SELECT owner_id FROM pairs WHERE engine_configuration_id=?',(engine_id,)).fetchone()
                if old:
                    if old['owner_id']!=owner:
                        raise ControlError('local_fixture_seed_owner_changed')
                    continue
                connections = [store._insert_connection(owner,provider,'synthetic-'+username+'-'+provider,
                    b'{"file_path":"/synthetic/fixture/not-a-credential"}','legacy_file') for provider in ('google','nextcloud')]
                store._insert_pair(owner,*connections,('google:'+engine_id,'nextcloud:'+engine_id),
                                   engine_id=engine_id,legacy=True)


def main():
    try:
        if os.environ.get('LOCAL_KEYCLOAK_FIXTURE')!='localhost-test-only':
            raise ControlError('local_fixture_flag_required')
        paths = Paths()
        command = sys.argv[1] if len(sys.argv)==2 else ''
        if command=='init':
            if os.geteuid()!=0:
                raise ControlError('local_fixture_setup_user_required')
            created = initialize(paths)
            print('Local fixture initialized' if created else 'Existing local fixture verified; keys/credentials retained')
        elif command=='seed':
            seed(paths)
            print('Synthetic Alice/Bob records seeded paused/dry-run; no provider credentials or writes')
        elif command=='credentials':
            data = verify(paths)
            print('LOCAL TEST ONLY — do not paste these generated passwords into issues')
            print('App: '+ORIGIN+'   Keycloak: '+IDENTITY)
            for name,entry in data['users'].items():
                print(name+': '+entry['password'])
            print('Keycloak admin: admin / '+data['admin']['password'])
        elif command=='ca':
            verify(paths)
            sys.stdout.write((paths.tls/'ca.crt').read_text())
        else:
            raise ControlError('local_fixture_unknown_command')
        return 0
    except Exception:
        print('local_fixture_failed; preserve volumes and inspect setup before retrying',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
