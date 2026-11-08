"""Offline container integration: real state/executor/health, fake providers only."""

from dataclasses import replace
from pathlib import Path
import tempfile

from calendar_sync.caldav import CalDAVPreconditionError, _resource
from calendar_sync.state import StateStore
from calendar_sync.worker import WorkerSettings, health_ok, run_worker


class FakeGoogle:
    calendar_id = 'offline@example.invalid'

    def list_events(self):
        return [{'id': 'offline-event', 'summary': 'Offline fixture',
                 'start': {'dateTime': '2026-11-08T09:00:00Z'}, 'end': {'dateTime': '2026-11-08T10:00:00Z'}}]


class FakeCalDAV:
    calendar_url = 'https://nextcloud.tcadc.com/remote.php/dav/calendars/offline/test/'

    def __init__(self):
        self.resources, self.put_count = {}, 0

    def list_resources(self):
        return list(self.resources.values())

    def get_resource(self, href):
        return self.resources.get(href)

    def put_resource(self, href, payload, *, create=False, expected_etag=None, allow_write=False):
        assert allow_write is True
        current = self.resources.get(href)
        if (create and current is not None) or (not create and (current is None or current.etag != expected_etag)):
            raise CalDAVPreconditionError('caldav_write_conflict')
        self.put_count += 1
        self.resources[href] = _resource(href, '"offline-'+str(self.put_count)+'"', payload.decode())


def main():
    google, caldav = FakeGoogle(), FakeCalDAV()
    with tempfile.TemporaryDirectory(prefix='worker-smoke-') as directory:
        root = Path(directory)
        settings = WorkerSettings.from_env(dict(GOOGLE_CALENDAR_ID=google.calendar_id,
            GOOGLE_TOKEN_FILE=str(root/'unused-token'), NEXTCLOUD_CALENDAR_URL=caldav.calendar_url,
            NEXTCLOUD_USERNAME='offline', NEXTCLOUD_PASSWORD_FILE=str(root/'unused-password'),
            SYNC_STATE_FILE=str(root/'state.sqlite3'), SYNC_HEALTH_FILE=str(root/'health.json')))
        for dry in (True, False, False):
            assert run_worker(replace(settings, dry_run=dry), once=True, google=google, caldav=caldav) == 0
            assert health_ok(settings.health_file, 60)
            if dry:
                assert caldav.put_count == 0
        assert caldav.put_count == 1 and len(caldav.resources) == 1
        with StateStore(settings.state_file) as store:
            assert store.connection.execute('SELECT count(*) FROM mappings').fetchone()[0] == 1
        print('PASS: offline worker dry-run, conditional create, restart idempotency and health')


if __name__ == '__main__':
    main()
