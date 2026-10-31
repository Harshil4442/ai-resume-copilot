"""Private V3-only fixed metadata adapter proposal; no SQL or mutation methods.

Three GETs, normal installed SDK identity, server partial response, no env values.
The size cap is postdecode; an owned main-thread alarm bounds the observation wall time.
This observational API does not establish maintenance closure or DDL permission.
"""
from __future__ import annotations

import json
import math
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

SDK = Path('/opt/homebrew/share/google-cloud-sdk/lib')
ACCOUNT = 'harshilyogeshbhai886@gmail.com'
PARENT = 'projects/ai-resume-parser-482412/locations/us-central1'
SERVICE_ID = 'ai-resume-parser'
API = PARENT + '/services/' + SERVICE_ID
SERVICE_FIELDS = ('name,uid,generation,observedGeneration,etag,reconciling,'
    'terminalCondition(type,state),latestCreatedRevision,latestReadyRevision,'
    'traffic(type,revision,percent,tag),trafficStatuses(type,revision,percent,tag)')
REVISION_FIELDS = ('revisions(name,uid,service,conditions(type,state,revisionReason,lastTransitionTime)),'
    'nextPageToken')
TOTAL_SECONDS = 6.0
SOCKET_SECONDS = 2.0
MAX_BYTES = 1024 * 1024
MAX_REVISIONS = 256
UUID = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\Z')
REVISION = re.compile(re.escape(API + '/revisions/') + r'[a-z0-9-]{1,63}\Z')


class MetadataUnavailable(RuntimeError):
    identity_diagnostic: dict[str, object]


def need(value, code):
    if not value:
        raise MetadataUnavailable('maintenance_metadata_' + code)


def _sdk():
    for path in (str(SDK), str(SDK / 'third_party')):
        if path not in sys.path:
            sys.path.insert(0, path)
    from apitools.base.py import encoding
    from googlecloudsdk.generated_clients.apis.run.v2 import run_v2_client, run_v2_messages
    return encoding, run_v2_client, run_v2_messages


def _normal_http():
    # Same source-bound normal-manager constructor as the existing native
    # reader; unlike its convenience read loop, this adapter never retries.
    import native_fresh_fence
    return native_fresh_fence.SDKGoogleReads(SDK, ACCOUNT)._http()


def project_service(value):
    allowed = {'name', 'uid', 'generation', 'observedGeneration', 'etag', 'reconciling',
        'terminalCondition', 'latestCreatedRevision', 'latestReadyRevision', 'traffic', 'trafficStatuses'}
    need(type(value) is dict and set(value) <= allowed and value.get('name') == API
        and UUID.fullmatch(value.get('uid', '')) and value.get('etag'), 'service_projection')
    for key in ('traffic', 'trafficStatuses'):
        rows = value.get(key)
        need(type(rows) is list and 0 < len(rows) <= 32, 'traffic_projection')
        need(all(type(row) is dict and set(row) <= {'type', 'revision', 'percent', 'tag'} for row in rows),
            'traffic_projection')
    condition = value.get('terminalCondition')
    need(type(condition) is dict and set(condition) <= {'type', 'state'}, 'service_condition_projection')
    return value


def project_revisions(value):
    need(type(value) is dict and set(value) <= {'revisions', 'nextPageToken'}, 'list_projection')
    need(not value.get('nextPageToken'), 'list_incomplete')
    rows = value.get('revisions')
    need(type(rows) is list and 0 < len(rows) <= MAX_REVISIONS, 'list_bound')
    seen: set[str] = set()
    seen_uids: set[str] = set()
    for row in rows:
        need(type(row) is dict and set(row) <= {'name', 'uid', 'service', 'conditions'}, 'revision_projection')
        if not (REVISION.fullmatch(row.get('name', '')) and UUID.fullmatch(row.get('uid', ''))
                and row.get('service') == SERVICE_ID and row['name'] not in seen
                and row['uid'] not in seen_uids):
            # A refused observation may expose ONLY this finite already-requested
            # metadata projection, never raw response/env/headers/error bodies.
            resource = re.compile(r'projects/[a-z0-9-]{1,63}/locations/[a-z0-9-]{1,63}/services/[a-z0-9-]{1,63}(?:/revisions/[a-z0-9-]{1,63})?\Z')
            simple = re.compile(r'[a-z0-9-]{1,63}\Z')
            def safe(value, resource=resource, simple=simple):
                return value if type(value) is str and (resource.fullmatch(value) or simple.fullmatch(value)) else None
            failure = MetadataUnavailable('maintenance_metadata_revision_identity')
            failure.identity_diagnostic = {'row_index': len(seen),
                'name_type': type(row.get('name')).__name__, 'name': safe(row.get('name')),
                'uid_type': type(row.get('uid')).__name__,
                'uid': row.get('uid') if type(row.get('uid')) is str and UUID.fullmatch(row['uid']) else None,
                'service_present': 'service' in row, 'service_type': type(row.get('service')).__name__,
                'service': safe(row.get('service')),
                'name_matches_fixed_parent': bool(REVISION.fullmatch(row.get('name', ''))),
                'service_equals_expected_short': row.get('service') == SERVICE_ID}
            raise failure
        seen.add(row['name'])
        seen_uids.add(row['uid'])
        conditions = row.get('conditions')
        need(type(conditions) is list and 0 < len(conditions) <= 16, 'conditions_projection')
        need(all(type(c) is dict and set(c) <= {'type', 'state', 'revisionReason', 'lastTransitionTime'}
            and type(c.get('type')) is str and 0 < len(c['type']) <= 64 for c in conditions), 'conditions_projection')
    return sorted(rows, key=lambda row: row['name'])


def require_retired(row):
    """The actual observed v2 triple; Ready alone does not prove inactivity.

    Source: root's sanitized native-v2-selected-conditions.json. The secondary
    ResourcesAvailable/RECONCILING/RETIRED is an actual retired diagnostic, not
    permission to infer reactivation. Unrelated healthy/error conditions remain
    irrelevant unless they contradict one of these three required conditions.
    """
    conditions = row.get('conditions')
    need(type(conditions) is list, 'retired_conditions_missing')
    keyed = {}
    for condition in conditions:
        name = condition.get('type')
        need(name not in keyed, 'duplicate_condition')
        keyed[name] = condition
    expected = {'Ready': 'CONDITION_SUCCEEDED', 'Active': 'CONDITION_FAILED',
        'ResourcesAvailable': 'CONDITION_RECONCILING'}
    transitions = set()
    for name, state in expected.items():
        condition = keyed.get(name, {})
        need(condition.get('state') == state and condition.get('revisionReason') == 'RETIRED',
            'not_retired')
        transition = condition.get('lastTransitionTime')
        need(type(transition) is str and re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3}|\.\d{6}|\.\d{9})?Z', transition),
            'retired_transition_missing')
        transitions.add(transition)
    need(len(transitions) == 1, 'retired_transition_inconsistent')


class ReadOnlyMaintenanceMetadata:
    """No constructor arguments: fixed controller owns route/auth/projection.

    Construct and authenticate before any supplied SQL transaction. Normal SDK
    credential refresh is confined to its existing token endpoint; resource
    reads are exactly GET, once each, with no redirect or transport retry.
    """
    def __init__(self):
        need(os.getenv('CLOUDSDK_CORE_DISABLE_FILE_LOGGING') == 'true'
            and os.getenv('CLOUDSDK_CORE_LOG_HTTP') == 'false', 'private_logging_required')
        # Match frozen NativeRun import order: native modules load before the
        # SDK prepends its legacy third_party packages (notably PyYAML).
        try:
            self.stock = _normal_http()
        except Exception:
            raise MetadataUnavailable('maintenance_metadata_normal_manager_unavailable') from None
        self.encoding, sdk_client, self.messages = _sdk()
        self.deadline = None
        self.closed = False
        self.owner_thread = threading.get_ident()
        self.started = False
        self._start_lock = threading.Lock()
        self.request_count = self.resource_write_sends = self.resource_get_sends = 0
        self._logical_sending = False
        self._physical_for_request = 0
        self._authorized_request = self.stock.session.request
        self.stock.session.trust_env, self.stock.session.max_redirects = False, 0
        for adapter in self.stock.session.adapters.values():
            adapter.max_retries = adapter.max_retries.__class__(total=0, connect=0, read=0, redirect=0, status=0)
        original_send = self.stock.session.send

        def send(request, **kwargs):
            target = urlsplit(request.url)
            if target.hostname == 'oauth2.googleapis.com':
                need(target.scheme == 'https' and target.path == '/token'
                    and target.username is None and target.password is None and target.port is None
                    and not target.fragment and request.method == 'POST' and not target.query, 'auth_route')
            else:
                self._route(request.url, request.method, request.body)
                need(self._logical_sending and self._physical_for_request == 0, 'resource_resend')
                self._physical_for_request += 1
                self.resource_get_sends += 1
            remaining = self._remaining()
            kwargs['timeout'] = min(SOCKET_SECONDS, remaining)
            kwargs['allow_redirects'] = False
            kwargs['stream'] = True
            response = original_send(request, **kwargs)
            try:
                need(response.status_code not in {401, 403}, 'authorization_refused')
                need(not 300 <= response.status_code < 400, 'redirect')
                self._remaining()
                # Cache the body under our alarm while retaining the exact
                # Response to close if a body read is interrupted. This size
                # bound is after download, before SDK decode, not a streaming
                # memory cap. Partial server fields exclude environment data.
                raw = response.content
                need(type(raw) is bytes and len(raw) <= MAX_BYTES, 'response_body_bound')
                self._remaining()
                return response
            finally:
                response.close()
        self.stock.session.send = send
        self.client = sdk_client.RunV2(url='https://run.googleapis.com/', get_credentials=False,
            http=self, log_request=False, log_response=False)
        self.client.num_retries = 0

    def _remaining(self):
        remaining = 0.0 if self.deadline is None else self.deadline - time.monotonic()
        need(math.isfinite(remaining) and remaining > 0, 'deadline')
        return remaining

    def _route(self, uri, method, body):
        target = urlsplit(uri)
        query = parse_qs(target.query, strict_parsing=True)
        need(target.scheme == 'https' and target.hostname == 'run.googleapis.com'
            and target.username is None and target.password is None and target.port is None
            and not target.fragment and method == 'GET' and body in (None, '', b''), 'fixed_read_route')
        need(query.get('alt', ['json']) == ['json'] and query.get('prettyPrint') == ['0'],
            'query')
        if unquote(target.path) == '/v2/' + API:
            need(set(query) <= {'alt', 'prettyPrint', 'fields'}
                and query.get('fields') == [SERVICE_FIELDS], 'service_fields')
        else:
            need(unquote(target.path) == '/v2/' + API + '/revisions'
                and set(query) <= {'alt', 'prettyPrint', 'fields', 'pageSize', 'showDeleted'}
                and query.get('fields') == [REVISION_FIELDS] and query.get('pageSize') == ['257']
                and query.get('showDeleted') == ['False'], 'list_fields')

    def request(self, uri, method='GET', body=None, headers=None, **kwargs):
        try:
            self._route(uri, method, body)
            self._remaining()
            need(self.request_count < 3 and self.stock.session.request is self._authorized_request,
                'request_ownership')
            self.request_count += 1
            kwargs['redirections'] = 0
            need(not self._logical_sending, 'request_nesting')
            self._logical_sending, self._physical_for_request = True, 0
            try:
                return self.stock.request(uri, method=method, body=body, headers=headers, **kwargs)
            finally:
                self._logical_sending = False
        except MetadataUnavailable:
            raise
        except Exception:
            # No vendor body/header/raw message may escape this boundary.
            raise MetadataUnavailable('maintenance_metadata_read_unavailable') from None

    def _decode(self, reply):
        value = self.encoding.MessageToDict(reply)
        need(len(json.dumps(value, allow_nan=False).encode()) <= MAX_BYTES, 'response_bound')
        return value

    def observe(self):
        with self._start_lock:
            need(not self.started, 'one_observation_only')
            self.started = True  # Even a failure before the first request consumes this object.
        need(threading.get_ident() == self.owner_thread and threading.current_thread() is threading.main_thread()
            and self.deadline is None and self.request_count == 0, 'observation_owner')
        need(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), 'foreign_alarm_active')
        previous_handler = signal.getsignal(signal.SIGALRM)
        def expired(_signum, _frame):
            raise MetadataUnavailable('maintenance_metadata_wall_deadline')
        started = time.monotonic()
        self.deadline = started + TOTAL_SECONDS
        try:
            signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, TOTAL_SECONDS)
            global_service = self.messages.StandardQueryParameters(fields=SERVICE_FIELDS, prettyPrint=False)
            global_list = self.messages.StandardQueryParameters(fields=REVISION_FIELDS, prettyPrint=False)
            get = self.messages.RunProjectsLocationsServicesGetRequest(name=API)
            before = project_service(self._decode(self.client.projects_locations_services.Get(get,
                global_params=global_service)))
            request = self.messages.RunProjectsLocationsServicesRevisionsListRequest(parent=API,
                pageSize=257, showDeleted=False)
            rows = project_revisions(self._decode(self.client.projects_locations_services_revisions.List(request,
                global_params=global_list)))
            after = project_service(self._decode(self.client.projects_locations_services.Get(get,
                global_params=global_service)))
            self._remaining()
            need(before == after, 'service_changed')
            need(self.request_count == self.resource_get_sends == 3 and self.resource_write_sends == 0,
                'exact_three_gets_required')
            return {'service': after, 'revisions': rows,
                'elapsed_ms': int((time.monotonic() - started) * 1000), 'resource_gets': self.request_count,
                'resource_write_sends': self.resource_write_sends, 'physical_resource_gets': self.resource_get_sends,
                'maintenance_admission_proved': False}
        except MetadataUnavailable:
            raise
        except Exception:
            raise MetadataUnavailable('maintenance_metadata_observation_unavailable') from None
        finally:
            try:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
            finally:
                try:
                    signal.signal(signal.SIGALRM, previous_handler)
                finally:
                    self.deadline = None
                    self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.stock.session.close()
            except Exception:
                raise MetadataUnavailable('maintenance_metadata_transport_close_unavailable') from None
