from dataclasses import replace
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from calendar_sync.cli import main
from calendar_sync.state import StateLockedError, StateStore
from calendar_sync.worker import WorkerSettings, health_ok, run_worker, shutdown_signals, write_health
from test_executor import MemoryCalDAV, source_event, URL


class FakeStop:
    def __init__(self, limit):
        self.limit, self.waits, self.stopped = limit, [], False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.waits.append(seconds)
        self.stopped = len(self.waits) >= self.limit
        return self.stopped


def report(*, healthy=True, dry=True):
    counts = {'create': 1, 'conflict': 0 if healthy else 1, 'pending': 0}
    return SimpleNamespace(status='dry_run' if dry else ('succeeded' if healthy else 'failed'),
                           plan=SimpleNamespace(counts=lambda: counts), counts=lambda: counts)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.environment = dict(GOOGLE_CALENDAR_ID='master@example.invalid',
            GOOGLE_TOKEN_FILE=str(self.root/'token.json'), NEXTCLOUD_CALENDAR_URL=URL,
            NEXTCLOUD_USERNAME='test', NEXTCLOUD_PASSWORD_FILE=str(self.root/'password'),
            SYNC_STATE_FILE=str(self.root/'state.sqlite3'), SYNC_HEALTH_FILE=str(self.root/'health.json'),
            SYNC_POLL_SECONDS='10', SYNC_MAX_BACKOFF_SECONDS='40', SYNC_HEALTH_MAX_AGE_SECONDS='100')
        self.settings = WorkerSettings.from_env(self.environment)
        self.google = Mock(calendar_id=self.settings.google_calendar_id)
        self.google.list_events.return_value = [source_event()]
        self.caldav = MemoryCalDAV()
        self.logs = []

    def run_cycle(self, **options):
        return run_worker(self.settings, google=self.google, caldav=self.caldav, emit=self.logs.append, **options)

    def test_settings_default_dry_run_and_no_secret_file_access(self):
        self.assertTrue(self.settings.dry_run)
        self.assertFalse(self.settings.allow_http_loopback)
        self.assertEqual(self.settings.configuration_id, 'master')
        self.assertNotIn('token.json', repr(self.settings))
        self.assertFalse(Path(self.settings.google_token_file).exists())

    def test_settings_reject_ambiguous_paths_booleans_ids_and_interval_bounds(self):
        for changes in ({'SYNC_DRY_RUN': 'yes'}, {'NEXTCLOUD_ALLOW_HTTP_LOOPBACK': '1'},
            {'GOOGLE_TOKEN_FILE': '$PWD/secrets/token.json'}, {'GOOGLE_TOKEN_FILE': 'relative'},
            {'GOOGLE_CALENDAR_ID': 'primary'}, {'GOOGLE_CALENDAR_ID': ''},
            {'SYNC_POLL_SECONDS': '0'}, {'SYNC_POLL_SECONDS': '1.5'},
            {'SYNC_MAX_BACKOFF_SECONDS': '5'}, {'SYNC_HEALTH_MAX_AGE_SECONDS': '10'},
            {'SYNC_HEALTH_FILE': self.settings.state_file},
            {'SYNC_HEALTH_FILE': self.settings.google_token_file},
            {'SYNC_HEALTH_FILE': self.settings.state_file+'.lock'},
            *({'SYNC_HEALTH_FILE': self.settings.state_file+suffix} for suffix in ('-wal', '-shm', '-journal'))):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '^worker_invalid_configuration$'):
                WorkerSettings.from_env(self.environment | changes)
        missing = dict(self.environment)
        del missing['NEXTCLOUD_USERNAME']
        with self.assertRaises(ValueError):
            WorkerSettings.from_env(missing)

    def test_settings_require_exact_origin_and_explicit_loopback_opt_in(self):
        local = self.environment | {'NEXTCLOUD_URL': 'http://localhost:8090',
                                   'NEXTCLOUD_CALENDAR_URL': 'http://localhost:8090/uat/'}
        with self.assertRaises(ValueError):
            WorkerSettings.from_env(local)
        self.assertTrue(WorkerSettings.from_env(local | {'NEXTCLOUD_ALLOW_HTTP_LOOPBACK': 'true'}).allow_http_loopback)
        for changes in ({'NEXTCLOUD_URL': 'http://192.168.1.1', 'NEXTCLOUD_CALENDAR_URL': 'http://192.168.1.1/uat/'},
                        {'NEXTCLOUD_URL': 'https://user:password@nextcloud.tcadc.com'}):
            with self.assertRaises(ValueError):
                WorkerSettings.from_env(self.environment | changes | {'NEXTCLOUD_ALLOW_HTTP_LOOPBACK': 'true'})

    def test_once_dry_run_real_executor_writes_no_applied_state(self):
        self.assertEqual(self.run_cycle(once=True), 0)
        self.assertEqual(self.caldav.puts, [])
        with StateStore(self.settings.state_file) as store:
            for table in ('runs', 'audit_entries', 'write_intents', 'mappings'):
                self.assertEqual(store.connection.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)
        self.assertTrue(health_ok(self.settings.health_file, 100))
        self.assertIn('"dry_run": true', self.logs[0])

    def test_live_restart_reuses_configuration_mapping_and_remote_identity(self):
        self.settings = replace(self.settings, dry_run=False)
        self.assertEqual(self.run_cycle(once=True), 0)
        self.assertEqual(self.run_cycle(once=True), 0)
        self.assertEqual(len(self.caldav.puts), 1)
        with StateStore(self.settings.state_file) as store:
            self.assertEqual(store.connection.execute('SELECT count(*) FROM mappings').fetchone()[0], 1)
            self.assertEqual(store.connection.execute('SELECT count(*) FROM runs').fetchone()[0], 2)

    def test_changed_calendar_or_credential_reference_refused_before_network(self):
        self.run_cycle(once=True)
        self.google.list_events.reset_mock()
        original = self.settings
        for changes in ({'google_calendar_id': 'different@example.invalid'},
                        {'google_token_file': str(self.root/'different.json')},
                        {'nextcloud_calendar_url': URL+'changed/'}):
            self.settings = replace(original, **changes)
            with self.assertRaisesRegex(ValueError, 'worker_configuration_changed'):
                self.run_cycle(once=True)
        self.google.list_events.assert_not_called()

    def test_competing_worker_does_not_touch_active_health_file(self):
        self.run_cycle(once=True)
        before = Path(self.settings.health_file).read_bytes()
        with StateStore(self.settings.state_file), self.assertRaises(StateLockedError):
            self.run_cycle(once=True)
        self.assertEqual(Path(self.settings.health_file).read_bytes(), before)

    def test_polling_backoff_is_jittered_bounded_and_resets_after_success(self):
        stop = FakeStop(6)
        execute = Mock(side_effect=[RuntimeError('PRIVATE'), RuntimeError('PRIVATE'), RuntimeError('PRIVATE'),
                                   RuntimeError('PRIVATE'), report(), report()])
        self.assertEqual(self.run_cycle(stop=stop, execute=execute, jitter=lambda: 1), 0)
        self.assertEqual(stop.waits, [11, 22, 40, 40, 11, 11])
        self.assertNotIn('PRIVATE', '\n'.join(self.logs))
        self.assertFalse(health_ok(self.settings.health_file, 100))
        self.assertEqual(json.loads(Path(self.settings.health_file).read_text())['status'], 'stopped')

    def test_failed_cycle_once_returns_unhealthy_and_next_invocation_recovers(self):
        self.assertEqual(self.run_cycle(once=True, execute=Mock(side_effect=RuntimeError('PRIVATE_TOKEN'))), 1)
        self.assertFalse(health_ok(self.settings.health_file, 100))
        self.assertEqual(self.run_cycle(once=True), 0)
        self.assertTrue(health_ok(self.settings.health_file, 100))
        self.assertNotIn('PRIVATE_TOKEN', '\n'.join(self.logs))

    def test_provider_retry_after_defers_next_cycle_and_can_be_interrupted(self):
        stop = FakeStop(1)
        def limited(*args, **kwargs):
            self.caldav.retry_after_seconds = 120
            self.google.retry_after_seconds = 180
            raise RuntimeError('rate limited')
        self.run_cycle(stop=stop, execute=limited, jitter=lambda: 0)
        self.assertEqual(stop.waits, [180])

    def test_conflict_in_dry_or_live_report_is_unhealthy(self):
        for dry in (True, False):
            self.settings = replace(self.settings, dry_run=dry)
            self.assertEqual(self.run_cycle(once=True, execute=Mock(return_value=report(healthy=False, dry=dry))), 1)
            self.assertFalse(health_ok(self.settings.health_file, 100))

    def test_health_checks_recency_modes_permissions_and_corrupt_inputs_without_sqlite(self):
        path = self.settings.health_file
        write_health(path, status='healthy', dry_run=True, finished_at=100)
        self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
        self.assertTrue(health_ok(path, 10, now=105))
        self.assertFalse(health_ok(path, 10, now=111))
        self.assertFalse(health_ok(path, 10, now=99))
        for status in ('starting', 'unhealthy', 'stopped'):
            write_health(path, status=status, dry_run=False, finished_at=100)
            self.assertFalse(health_ok(path, 10, now=105))
        for data in ('{', '[]', 'null', '{"version":1,"status":"healthy","dry_run":true,"finished_at":NaN}'):
            Path(path).write_text(data)
            self.assertFalse(health_ok(path, 10, now=105))
        self.assertFalse(health_ok(self.root/'missing', 10, now=105))

    def test_health_atomic_replace_failure_preserves_previous_file(self):
        path = Path(self.settings.health_file)
        write_health(path, status='healthy', dry_run=True, finished_at=100)
        before = path.read_bytes()
        with patch('calendar_sync.worker.os.replace', side_effect=OSError('PRIVATE')):
            with self.assertRaises(OSError):
                write_health(path, status='unhealthy', dry_run=True)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.health-*')), [])

    def test_cli_worker_once_routes_flags_and_sanitizes_startup_errors(self):
        with patch.dict(os.environ, self.environment, clear=True), patch('calendar_sync.worker.run_worker', return_value=0) as run:
            self.assertEqual(main(['worker', '--once']), 0)
            self.assertTrue(run.call_args.kwargs['once'])
            self.assertTrue(run.call_args.args[0].dry_run)
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(output):
            self.assertEqual(main(['worker']), 2)
        self.assertEqual(output.getvalue().strip(), 'worker_startup_or_state_failed')

    def test_cli_health_reads_status_without_configuration_or_provider_calls(self):
        write_health(self.settings.health_file, status='healthy', dry_run=True, finished_at=time.time())
        with patch.dict(os.environ, {'SYNC_HEALTH_FILE': self.settings.health_file}, clear=True):
            self.assertEqual(main(['health']), 0)
        for maximum in ('invalid', '-1', '0'):
            with patch.dict(os.environ, {'SYNC_HEALTH_MAX_AGE_SECONDS': maximum}, clear=True):
                self.assertEqual(main(['health']), 1)

    def test_signals_set_stop_and_restore_prior_handlers(self):
        for number in (signal.SIGTERM, signal.SIGINT):
            before = signal.getsignal(number)
            stop = threading.Event()
            with shutdown_signals(stop):
                os.kill(os.getpid(), number)
                self.assertTrue(stop.is_set())
            self.assertEqual(signal.getsignal(number), before)

    def test_real_process_sigterm_and_sigint_stop_poll_wait_and_release_lock(self):
        script = '''
import json,sys,threading
from types import SimpleNamespace
from calendar_sync.worker import WorkerSettings,run_worker,shutdown_signals
settings=WorkerSettings.from_env(json.loads(sys.argv[1]))
stop=threading.Event()
report=SimpleNamespace(status='dry_run',plan=SimpleNamespace(counts=lambda: {}))
with shutdown_signals(stop):
    code=run_worker(settings,stop=stop,google=SimpleNamespace(calendar_id=settings.google_calendar_id),
        caldav=SimpleNamespace(calendar_url=settings.nextcloud_calendar_url),execute=lambda *a,**k: report)
sys.exit(code)
'''
        for number in (signal.SIGTERM, signal.SIGINT):
            Path(self.settings.health_file).unlink(missing_ok=True)
            process = subprocess.Popen([sys.executable, '-c', script, json.dumps(self.environment)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic()+10
                while not health_ok(self.settings.health_file, 100):
                    if process.poll() is not None or time.monotonic() > deadline:
                        self.fail('Worker did not become ready')
                    time.sleep(0.02)
                process.send_signal(number)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertNotIn('Traceback', stderr)
                self.assertFalse(health_ok(self.settings.health_file, 100))
                with StateStore(self.settings.state_file):
                    pass
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)

    def test_google_retry_sleep_is_interruptible_without_waiting_for_backoff(self):
        stop = threading.Event()
        def fake_execute(*args, **kwargs):
            stop.set()
            raise RuntimeError('shutdown')
        with patch('calendar_sync.worker.GoogleCalendar') as constructor:
            constructor.return_value.calendar_id = self.settings.google_calendar_id
            run_worker(self.settings, stop=stop, caldav=self.caldav, execute=fake_execute, emit=self.logs.append,
                       google=None)
            sleeper = constructor.call_args.kwargs['sleep']
            with self.assertRaises(Exception):
                sleeper(60)
