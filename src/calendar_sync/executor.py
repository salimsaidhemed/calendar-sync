"""Opt-in conditional execution with durable intents and no deletion path."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from uuid import uuid4

from calendar_sync.caldav import CalDAVError, CalDAVPreconditionError
from calendar_sync.planner import (
    PlanningError, SyncPlan, build_plan, event_identity, owned_resource_matches, preview_sync, serialize_owned_event,
)
from calendar_sync.state import StateConflictError


class ExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class ExecutionItem:
    source_id: str | None
    action: str
    outcome: str
    reason: str
    operation_id: str | None = None


@dataclass(frozen=True, repr=False)
class ExecutionReport:
    dry_run: bool
    plan: SyncPlan
    items: tuple[ExecutionItem, ...]
    run_id: str | None
    status: str

    def counts(self):
        observed = Counter(item.outcome for item in self.items)
        return {key: observed[key] for key in ('applied', 'unchanged', 'skipped', 'conflict', 'pending')}


def _mapping_matches(store, intent):
    row = store.get_mapping(intent['configuration_id'], intent['source_event_id'])
    if intent['action'] == 'create':
        return row is None
    return row is not None and all((
        row['destination_href'] == intent['destination_href'],
        row['destination_uid'] == intent['destination_uid'],
        row['source_fingerprint'] == intent['baseline_fingerprint'],
        row['destination_etag'] == intent['expected_etag'],
    ))


def _handle_intent(store, context, intent, run_id, google, caldav, maximum, resources):
    configuration_id = context.configuration_id
    operation_id = intent['id']
    identity = event_identity(context, intent['source_event_id'])

    def note(reason, conflict=False):
        store.note_intent(configuration_id, operation_id, run_id, reason=reason, conflict=conflict)
        return ExecutionItem(intent['source_event_id'], intent['action'],
                             'conflict' if conflict else 'pending', reason, operation_id)

    def desired(resource):
        return owned_resource_matches(context, intent['source_event_id'], intent['source_fingerprint'],
                                      resource, operation_id=operation_id)

    def complete(resource):
        try:
            store.complete_intent(configuration_id, operation_id, run_id, resource.etag)
        except StateConflictError:
            return note('execution_local_mapping_changed', True)
        return ExecutionItem(intent['source_event_id'], intent['action'], 'applied', 'write_confirmed', operation_id)

    if (intent['owner_marker'] != context.owner_marker() or intent['destination_href'] != identity.href
            or intent['destination_uid'] != identity.uid):
        return note('intent_scope_changed', True)
    if not _mapping_matches(store, intent):
        return note('execution_local_mapping_changed', True)
    if any(resource.uid == identity.uid and resource.href != identity.href for resource in resources):
        return note('destination_uid_collision', True)
    try:
        current = caldav.get_resource(identity.href)
    except Exception:
        return note('destination_read_failed')
    if desired(current):
        if intent.get('attempts', 0) == 0:
            return note('unattempted_intent_collision', True)
        return complete(current)
    if intent['action'] == 'create':
        if current is not None:
            return note('destination_appeared', True)
    elif (current is None or current.etag != intent['expected_etag']
          or not owned_resource_matches(context, intent['source_event_id'], intent['baseline_fingerprint'], current)):
        return note('destination_baseline_changed', True)
    try:
        fresh = build_plan(context, google.list_events(), (), (), source_complete=True, destination_complete=True)
        event = next((item.event for item in fresh.items if item.source_id == intent['source_event_id']), None)
    except Exception:
        return note('source_revalidation_failed')
    if event is None or event.fingerprint() != intent['source_fingerprint']:
        return note('source_changed_during_recovery', True)
    try:
        payload = serialize_owned_event(context, event, stamp=datetime.fromisoformat(intent['stamp']),
                                        operation_id=operation_id)
    except Exception:
        return note('payload_reconstruction_changed', True)
    if hashlib.sha256(payload).hexdigest() != intent['payload_hash']:
        return note('payload_reconstruction_changed', True)
    if not store.mark_intent_attempt(configuration_id, operation_id, maximum):
        return note('write_retry_limit')
    precondition_failed = False
    try:
        caldav.put_resource(identity.href, payload, create=intent['action'] == 'create',
                            expected_etag=intent['expected_etag'], allow_write=True)
    except CalDAVPreconditionError:
        precondition_failed = True
    except Exception:
        pass
    try:
        observed = caldav.get_resource(identity.href)
    except Exception:
        return note('write_readback_failed')
    if desired(observed):
        return complete(observed)
    if precondition_failed:
        return note('write_precondition_failed', True)
    return note('write_not_confirmed')


def execute_sync(store, configuration_id, google, caldav, *, dry_run=True, max_put_attempts=3):
    if type(dry_run) is not bool or type(max_put_attempts) is not int or not 1 <= max_put_attempts <= 10:
        raise ExecutionError('execution_invalid_options')
    plan = preview_sync(store, configuration_id, google, caldav)
    if dry_run:
        return ExecutionReport(True, plan, (), None, 'dry_run')
    store.interrupt_execution_runs(configuration_id)
    run_id = store.start_run(configuration_id, dry_run=False, purpose='execution')
    results = []
    try:
        context = plan.context
        pending = store.pending_intents(configuration_id)
        handled = set()
        if pending:
            resources = caldav.list_resources()
            for intent in pending:
                handled.add(intent['source_event_id'])
                results.append(_handle_intent(store, context, intent, run_id,
                    google, caldav, max_put_attempts, resources))
            plan = preview_sync(store, configuration_id, google, caldav)
            if plan.context != context:
                raise ExecutionError('execution_configuration_changed')
        for item in plan.items:
            if item.source_id in handled:
                continue
            if item.action not in {'create', 'update'}:
                outcome = 'unchanged' if item.action == 'unchanged' else ('conflict' if item.action == 'conflict' else 'skipped')
                store.append_audit(configuration_id, run_id, item.action, 'skipped',
                                   source_event_id=item.source_id, reason_code=item.reason)
                results.append(ExecutionItem(item.source_id, item.action, outcome, item.reason))
                continue
            mapping = store.get_mapping(configuration_id, item.source_id)
            if item.action == 'update' and (mapping is None or mapping['destination_etag'] != item.expected_etag):
                store.append_audit(configuration_id, run_id, 'conflict', 'skipped',
                                   source_event_id=item.source_id, reason_code='execution_local_mapping_changed')
                results.append(ExecutionItem(item.source_id, item.action, 'conflict', 'execution_local_mapping_changed'))
                continue
            operation_id = str(uuid4())
            stamp = datetime.now(timezone.utc).replace(microsecond=0)
            payload = serialize_owned_event(context, item.event, stamp=stamp, operation_id=operation_id)
            values = dict(id=operation_id, source_event_id=item.source_id, action=item.action,
                destination_href=item.destination_href, destination_uid=item.destination_uid,
                source_fingerprint=item.source_fingerprint, owner_marker=context.owner_marker(),
                payload_hash=hashlib.sha256(payload).hexdigest(), stamp=stamp.isoformat(),
                expected_etag=item.expected_etag,
                baseline_fingerprint=mapping['source_fingerprint'] if item.action == 'update' else None)
            store.prepare_intent(configuration_id, run_id, values)
            intent = store.get_intent(configuration_id, operation_id)
            results.append(_handle_intent(store, context, intent, run_id, google,
                                          caldav, max_put_attempts, ()))
        status = 'failed' if any(item.outcome in {'conflict', 'pending'} for item in results) else 'succeeded'
        store.finish_run(configuration_id, run_id, status, reason_code='execution_complete')
        return ExecutionReport(False, plan, tuple(results), run_id, status)
    except Exception as error:
        try:
            store.finish_run(configuration_id, run_id, 'failed', reason_code='execution_interrupted')
        except Exception:
            pass
        if isinstance(error, (CalDAVError, PlanningError, ExecutionError)):
            raise
        raise ExecutionError('execution_state_or_provider_failed') from None
