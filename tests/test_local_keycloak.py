import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from calendar_sync.control import ControlError, ControlStore, OwnerContext
from examples.keycloak.fixture import CLIENT, ISSUER, ORIGIN, Paths, initialize, seed, verify
from examples.keycloak.test_local import Forms, local_url


class LocalKeycloakTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.paths = Paths.under(Path(directory.name).resolve())

    def initialize(self):
        return initialize(self.paths,app_uid=os.geteuid(),identity_uid=os.geteuid())

    def test_realm_has_exact_confidential_pkce_audience_roles_and_random_credentials(self):
        self.initialize()
        credentials = verify(self.paths)
        realm = json.loads((self.paths.imports/'realm.json').read_text())
        client = realm['clients'][0]
        self.assertFalse(client['publicClient'])
        for flag in ('implicitFlowEnabled','directAccessGrantsEnabled','serviceAccountsEnabled'):
            self.assertFalse(client[flag])
        self.assertEqual(client['attributes']['pkce.code.challenge.method'],'S256')
        self.assertEqual(client['redirectUris'],[ORIGIN+'/auth/callback'])
        self.assertEqual(client['attributes']['backchannel.logout.url'],ORIGIN+'/auth/backchannel-logout')
        mappers = {item['protocolMapper']:item['config'] for item in client['protocolMappers']}
        self.assertEqual(mappers['oidc-audience-mapper']['included.client.audience'],CLIENT)
        self.assertEqual(mappers['oidc-usermodel-client-role-mapper']['id.token.claim'],'true')
        for user in realm['users']:
            self.assertEqual(bool(user.get('clientRoles')),user['username']!='denied')
        secrets = [credentials['client_secret'],credentials['admin']['password']]+[
            entry['password'] for entry in credentials['users'].values()]
        self.assertEqual(len(set(secrets)),5)
        self.assertTrue(all(len(value)>=40 for value in secrets))
        for path in (self.paths.metadata/'credentials.json',self.paths.imports/'realm.json',self.paths.tls/'server.key'):
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_tls_is_signed_for_localhost_and_ca_signing_key_is_not_persisted(self):
        self.initialize()
        ca = x509.load_pem_x509_certificate((self.paths.tls/'ca.crt').read_bytes())
        leaf = x509.load_pem_x509_certificate((self.paths.tls/'server.crt').read_bytes())
        leaf.verify_directly_issued_by(ca)
        self.assertTrue(ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertFalse(leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertEqual(leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName),['localhost'])
        self.assertIn(ExtendedKeyUsageOID.SERVER_AUTH,leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)
        self.assertEqual((self.paths.tls/'ca.crt').stat().st_mode & 0o777,0o644)
        self.assertFalse(any('ca.key' in str(path) for path in self.paths.metadata.parent.rglob('*')))

    def test_reinitialize_preserves_credentials_keys_and_seeded_state(self):
        self.assertTrue(self.initialize())
        before = {name:path.read_bytes() for name,path in self.paths.immutable().items()}
        seed(self.paths)
        self.assertFalse(self.initialize())
        self.assertEqual(before,{name:path.read_bytes() for name,path in self.paths.immutable().items()})

    def test_partial_and_tampered_fixtures_are_refused_without_overwrite(self):
        self.paths.metadata.mkdir()
        foreign = self.paths.metadata/'foreign'
        foreign.write_text('preserve')
        with self.assertRaises(ControlError):
            self.initialize()
        self.assertEqual(foreign.read_text(),'preserve')
        foreign.unlink()
        self.initialize()
        key = self.paths.keys/'session.key'
        key.write_bytes(b'x'*32)
        with self.assertRaises(ControlError):
            self.initialize()
        self.assertEqual(key.read_bytes(),b'x'*32)

    def test_seed_is_idempotent_owner_scoped_paused_and_has_no_provider_credentials(self):
        self.initialize()
        credentials = verify(self.paths)
        seed(self.paths)
        seed(self.paths)
        with ControlStore(self.paths.settings().control_file,self.paths.settings().control_key_file) as store:
            self.assertEqual(store.connection.execute('SELECT count(*) FROM owners').fetchone()[0],2)
            self.assertEqual(store.connection.execute('SELECT count(*) FROM pairs').fetchone()[0],2)
            for name in ('alice','bob'):
                context = OwnerContext(ISSUER,credentials['users'][name]['subject'])
                connections = store.list_connections(context)
                self.assertEqual(len(connections),2)
                self.assertTrue(all(row['status']=='needs_verification' for row in connections))
            self.assertTrue(all(row['state']=='paused' and row['dry_run']==1 and row['legacy_review']==1
                for row in store.connection.execute('SELECT * FROM pairs')))

    def test_test_driver_refuses_external_urls_and_parses_forms_without_scripts(self):
        for url in ('http://localhost:8443','https://evil.invalid/','https://localhost:9443.evil.invalid/',
                    'https://user:password@localhost:9443/','https://localhost:8443/#fragment'):
            with self.assertRaises(ValueError):
                local_url(url)
        self.assertEqual(local_url(ORIGIN+'/auth/callback?state=test'),ORIGIN+'/auth/callback?state=test')
        form = Forms('<form id="kc-form-login" action="/login?a=1&amp;b=2"><input name="username" value="alice"></form>').forms[0]
        self.assertEqual(form['action'],'/login?a=1&b=2')
        self.assertEqual(form['inputs'],{'username':'alice'})
