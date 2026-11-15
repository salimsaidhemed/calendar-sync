"""Single-process polling worker; diagnostics contain only fixed codes/counts."""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import random
import signal
import tempfile
import threading
import time

from calendar_sync.caldav import CalDAVCalendar
from calendar_sync.executor import ExecutionStopped, execute_sync
from calendar_sync.google_calendar import GoogleCalendar
from calendar_sync.planner import PlanContext
from calendar_sync.state import StateStore


def _boolean(environment, name, default):
    value = environment.get(name, default)
    if value not in {'true', 'false'}:
        raise ValueError('worker_invalid_boolean')
    return value == 'true'


def _integer(environment, name, default, minimum, maximum):
    try:
        value = int(environment.get(name, str(default)))
        if not minimum <= value <= maximum:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValueError('worker_invalid_interval') from None


def _path(environment, name, default=None):
    value = environment.get(name, default)
    if not isinstance(value, str) or not value or '$' in value or not Path(value).is_absolute():
        raise ValueError('worker_absolute_paths_required')
    return str(Path(value).resolve())


@dataclass(frozen=True, repr=False)
class WorkerSettings:
    configuration_id: str
    google_calendar_id: str
    google_token_file: str
    nextcloud_calendar_url: str
    nextcloud_origin: str
    nextcloud_username: str
    nextcloud_password_file: str
    state_file: str
    health_file: str
    dry_run: bool = True
    allow_http_loopback: bool = False
    poll_seconds: int = 300
    max_backoff_seconds: int = 3600
    health_max_age_seconds: int = 7200

    @classmethod
    def from_env(cls, environment):
        try:
            settings = cls(
                configuration_id=environment.get('SYNC_CONFIGURATION_ID', 'master'),
                google_calendar_id=environment['GOOGLE_CALENDAR_ID'],
                google_token_file=_path(environment, 'GOOGLE_TOKEN_FILE'),
                nextcloud_calendar_url=environment['NEXTCLOUD_CALENDAR_URL'],
                nextcloud_origin=environment.get('NEXTCLOUD_URL', 'https://nextcloud.tcadc.com'),
                nextcloud_username=environment['NEXTCLOUD_USERNAME'],
                nextcloud_password_file=_path(environment, 'NEXTCLOUD_PASSWORD_FILE'),
                state_file=_path(environment, 'SYNC_STATE_FILE', '/data/calendar-sync.sqlite3'),
                health_file=_path(environment, 'SYNC_HEALTH_FILE', '/data/health.json'),
                dry_run=_boolean(environment, 'SYNC_DRY_RUN', 'true'),
                allow_http_loopback=_boolean(environment, 'NEXTCLOUD_ALLOW_HTTP_LOOPBACK', 'false'),
                poll_seconds=_integer(environment, 'SYNC_POLL_SECONDS', 300, 1, 86400),
                max_backoff_seconds=_integer(environment, 'SYNC_MAX_BACKOFF_SECONDS', 3600, 1, 86400),
                health_max_age_seconds=_integer(environment, 'SYNC_HEALTH_MAX_AGE_SECONDS', 7200, 1, 172800),
            )
            context = PlanContext(settings.configuration_id, settings.google_calendar_id, settings.nextcloud_calendar_url)
            calendar = settings.caldav()
            if context.destination_calendar_url != calendar.calendar_url:
                raise ValueError()
            files = [settings.state_file, settings.health_file, settings.google_token_file, settings.nextcloud_password_file]
            if len(set(files)) != len(files) or any(a == b + suffix for a in files for b in files
                                                   for suffix in ('.lock', '-wal', '-shm', '-journal')):
                raise ValueError()
            if settings.max_backoff_seconds < settings.poll_seconds or settings.health_max_age_seconds <= settings.poll_seconds:
                raise ValueError()
            return settings
        except Exception:
            raise ValueError('worker_invalid_configuration') from None

    def caldav(self, *, should_stop=lambda: False):
        return CalDAVCalendar(self.nextcloud_calendar_url, self.nextcloud_username, self.nextcloud_password_file,
                              origin_url=self.nextcloud_origin, allow_http_loopback=self.allow_http_loopback,
                              should_stop=should_stop)


def write_health(path, *, status, dry_run, finished_at=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.health-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(dict(version=1, status=status, dry_run=dry_run, finished_at=finished_at), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def health_ok(path, max_age, *, now=None):
    try:
        with open(path) as stream:
            value = json.loads(stream.read(4097))
        stamp = value['finished_at']
        age = (time.time() if now is None else now) - stamp
        return (value['version'] == 1 and value['status'] == 'healthy' and type(value['dry_run']) is bool
                and type(stamp) in {float, int} and math.isfinite(stamp) and 0 <= age <= max_age)
    except Exception:
        return False


@contextmanager
def shutdown_signals(stop):
    previous = {}
    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, lambda signum, frame: stop.set())
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run_worker(settings, *, once=False, stop=None, clock=time.time, jitter=random.random,
               google=None, caldav=None, execute=execute_sync, emit=print):
    stop = stop if stop is not None else threading.Event()

    def retry_sleep(seconds):
        if stop.wait(seconds):
            raise ExecutionStopped('execution_stopped')

    google = google if google is not None else GoogleCalendar(settings.google_token_file, settings.google_calendar_id,
                                                            sleep=retry_sleep, should_stop=stop.is_set)
    caldav = caldav if caldav is not None else settings.caldav(should_stop=stop.is_set)
    failures = 0
    with StateStore(settings.state_file) as store:
        existing = store.connection.execute('SELECT * FROM configurations WHERE id=?', (settings.configuration_id,)).fetchone()
        if existing is None:
            store.create_configuration(settings.configuration_id, 'operator', google.calendar_id, caldav.calendar_url,
                google_credential_file=settings.google_token_file, nextcloud_credential_file=settings.nextcloud_password_file)
        else:
            references = dict(store.connection.execute('SELECT provider,file_path FROM credential_references '
                'WHERE configuration_id=?', (settings.configuration_id,)))
            if (existing['google_calendar_id'] != settings.google_calendar_id
                    or existing['nextcloud_calendar_url'] != settings.nextcloud_calendar_url
                    or references != {'google': settings.google_token_file, 'nextcloud': settings.nextcloud_password_file}):
                raise ValueError('worker_configuration_changed')
        write_health(settings.health_file, status='starting', dry_run=settings.dry_run)
        try:
            while not stop.is_set():
                google.retry_after_seconds = caldav.retry_after_seconds = 0
                try:
                    report = execute(store, settings.configuration_id, google, caldav, dry_run=settings.dry_run,
                                     should_stop=stop.is_set)
                    if stop.is_set():
                        break
                    counts = report.plan.counts() if settings.dry_run else report.counts()
                    success = not counts.get('conflict', 0) and not counts.get('pending', 0)
                    success = success and report.status in {'succeeded', 'dry_run'}
                    emit('worker_cycle ' + json.dumps(dict(status='healthy' if success else 'unhealthy',
                                                          dry_run=settings.dry_run, counts=counts), sort_keys=True))
                except ExecutionStopped:
                    break
                except Exception:
                    if stop.is_set():
                        break
                    success = False
                    emit('worker_cycle_failed')
                write_health(settings.health_file, status='healthy' if success else 'unhealthy',
                             dry_run=settings.dry_run, finished_at=clock())
                failures = 0 if success else min(failures + 1, 17)
                if once:
                    return 0 if success else 1
                base = settings.poll_seconds if success else min(settings.max_backoff_seconds,
                    settings.poll_seconds * 2 ** (failures - 1))
                delay = min(settings.max_backoff_seconds, base + base * 0.1 * jitter())
                delay = max(delay, google.retry_after_seconds, caldav.retry_after_seconds)
                stop.wait(delay)
            return 0
        finally:
            if not once or stop.is_set():
                write_health(settings.health_file, status='stopped', dry_run=settings.dry_run)
