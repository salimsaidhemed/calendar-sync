from contextlib import closing, redirect_stdout
import io
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from calendar_sync.state import StateStore


class OperationsDocsTests(unittest.TestCase):
    """Execute the actual documented relocation block against private fake state."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.state = self.root/'state.sqlite3'
        self.google = self.root/'new-google'
        self.nextcloud = self.root/'new-nextcloud'
        for path in (self.google, self.nextcloud):
            path.write_text('fixture only')
            path.chmod(0o600)
        self.environment = dict(SYNC_CONFIGURATION_ID='selected', GOOGLE_CALENDAR_ID='source@example.invalid',
            NEXTCLOUD_URL='https://nextcloud.tcadc.com',
            NEXTCLOUD_CALENDAR_URL='https://nextcloud.tcadc.com/remote.php/dav/calendars/test/calendar/',
            NEXTCLOUD_USERNAME='test', SYNC_STATE_FILE=str(self.state), SYNC_HEALTH_FILE=str(self.root/'health.json'),
            GOOGLE_TOKEN_FILE=str(self.google), NEXTCLOUD_PASSWORD_FILE=str(self.nextcloud),
            OLD_GOOGLE_TOKEN_FILE='/old/google', OLD_NEXTCLOUD_PASSWORD_FILE='/old/nextcloud')
        with StateStore(self.state) as store:
            for configuration in ('selected', 'other'):
                store.create_configuration(configuration, 'operator', self.environment['GOOGLE_CALENDAR_ID'],
                    self.environment['NEXTCLOUD_CALENDAR_URL'], google_credential_file='/old/google',
                    nextcloud_credential_file='/old/nextcloud')
            run = store.start_run('selected', dry_run=False)
            store.record_applied('selected', run, source_event_id='source', destination_href='event.ics',
                destination_uid='uid', source_fingerprint='baseline', destination_etag='"etag"', action='create')
            store.finish_run('selected', run, 'succeeded')
        guide = (Path(__file__).resolve().parents[1]/'docs'/'operations.md').read_text()
        section = guide.split('## Scoped credential-path relocation', 1)[1]
        self.block = re.search(r"python - <<'PY'\n(.*?)\nPY", section, re.S).group(1)

    def read_state(self):
        with closing(sqlite3.connect(self.state)) as connection:
            return '\n'.join(connection.iterdump())

    def execute_block(self, environment):
        output = io.StringIO()
        with patch.dict(os.environ, environment, clear=True), redirect_stdout(output):
            exec(compile(self.block, 'docs/operations.md relocation block', 'exec'), {})
        return output.getvalue()

    def test_documented_relocation_preserves_mapping_history_and_other_configuration(self):
        with StateStore(self.state) as store:
            planning = store.planning_snapshot('selected')
            audit = [dict(row) for row in store.list_audit('selected')]
        self.assertIn('PASS: scoped', self.execute_block(self.environment))
        with StateStore(self.state) as store:
            self.assertEqual(store.planning_snapshot('selected'), planning)
            self.assertEqual([dict(row) for row in store.list_audit('selected')], audit)
            references = dict(store.connection.execute('SELECT provider,file_path FROM credential_references '
                "WHERE configuration_id='selected'"))
            self.assertEqual(references, {'google': str(self.google), 'nextcloud': str(self.nextcloud)})
            other = dict(store.connection.execute('SELECT provider,file_path FROM credential_references '
                "WHERE configuration_id='other'"))
            self.assertEqual(other, {'google': '/old/google', 'nextcloud': '/old/nextcloud'})

    def test_documented_relocation_rolls_back_wrong_old_reference_and_refuses_calendar_change(self):
        before = self.read_state()
        for changes in ({'OLD_NEXTCLOUD_PASSWORD_FILE': '/incorrect'}, {'GOOGLE_CALENDAR_ID': 'changed@example.invalid'}):
            with self.assertRaises(ValueError):
                self.execute_block(self.environment | changes)
            self.assertEqual(self.read_state(), before)
