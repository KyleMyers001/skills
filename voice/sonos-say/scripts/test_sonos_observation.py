"""Real SDK read-error and deadline contracts; no LAN access."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


SDK = r'''
import builtins
import html
import socket
from unittest.mock import patch
import requests
import soco
import sonos_topology as t

def forbidden(*args, **kwargs):
    raise AssertionError('network prohibited')
socket.socket.connect = forbidden
socket.create_connection = forbidden
speaker = soco.SoCo('192.0.2.42')
speaker._household_id = 'observation-contract'
speaker._uid = 'target'
def xml(coordinator='target'):
    return ('<ZoneGroups><ZoneGroup Coordinator="' + coordinator + '" ID="g">'
            '<ZoneGroupMember UUID="target" ZoneName="Target" '
            'Location="http://192.0.2.42:1400/xml/device_description.xml"/>'
            '</ZoneGroup></ZoneGroups>')
def response(status=200, payload=None):
    r = requests.Response()
    r.status_code = status
    r.url = 'http://192.0.2.42:1400/mock'
    text = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            '<s:Body><u:GetZoneGroupStateResponse xmlns:u="urn:test">'
            '<ZoneGroupState>' + html.escape(payload or xml()) + '</ZoneGroupState>'
            '</u:GetZoneGroupStateResponse></s:Body></s:Envelope>')
    r._content = (text if status == 200 else 'unavailable').encode()
    return r
class Clock:
    now = 0.0
    def __init__(self): self.sleeps = []
    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay
'''


class TestObservationContracts(unittest.TestCase):
    def sdk(self, body):
        result = subprocess.run(
            [sys.executable, '-B', '-c', SDK + body],
            env={'PATH': os.defpath, 'PYTHONPATH': str(Path(__file__).parent),
                 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_poll_keeps_read_error_if_scheduler_uses_remaining_dispatch_budget(self):
        self.sdk(r'''
clock = Clock()
original = t.TopologyUnavailable('network failure before scheduling delay')
def monotonic():
    observed = clock.now
    if observed >= 0.5: clock.now += 0.11
    return observed
with patch.object(t.time, 'monotonic', monotonic), \
     patch.object(t.time, 'sleep', clock.sleep):
    try: t._poll(lambda limit: (_ for _ in ()).throw(original), 1.0)
    except t.TopologyUnavailable as error: assert error is original, error
    else: raise AssertionError('failure accepted')
assert clock.sleeps == [0.5]
''')

    def test_poll_skips_unusable_sleep_and_preserves_last_observation_error(self):
        self.sdk(r'''
for timeout, expected_reads, expected_sleeps in ((0.5, 1, []), (0.7, 1, []),
                                               (1.0, 2, [0.5]), (1.2, 2, [0.5])):
    clock, calls = Clock(), []
    original = t.TopologyUnavailable('actual connection failure')
    def read(limit):
        calls.append(limit)
        raise original
    with patch.object(t.time, 'monotonic', lambda: clock.now), \
         patch.object(t.time, 'sleep', clock.sleep):
        try: t._poll(read, timeout)
        except t.TopologyUnavailable as error:
            assert error is original, (timeout, error)
        else: raise AssertionError('failure accepted')
    assert clock.sleeps == expected_sleeps, (timeout, clock.sleeps)
    assert len(calls) == expected_reads, calls
for timeout, reads in ((0, 1), (0.2, 0), (0.499, 0), (0.5, 1)):
    clock, calls = Clock(), []
    with patch.object(t.time, 'monotonic', lambda: clock.now), \
         patch.object(t.time, 'sleep', clock.sleep):
        try: t._poll(lambda limit: calls.append(limit) or 'valid', timeout)
        except t.TopologyUnavailable:
            assert reads == 0
    assert len(calls) == reads
    assert not clock.sleeps
''')

    def test_real_parser_missing_coordinator_retries_only_convergence(self):
        self.sdk(r'''
clock = Clock()
with patch.object(t.time, 'monotonic', lambda: clock.now), \
     patch.object(t.time, 'sleep', clock.sleep), \
     patch('soco.services.requests.post', side_effect=[response(payload=xml('missing')), response()]) as post:
    t._wait_for_topology(speaker, 'target', frozenset({'target'}), 2)
    assert post.call_count == 2
with patch('soco.services.requests.post', return_value=response()) as post:
    plan = t.validate_announcement_target(speaker)
for check in (t.validate_announcement_target, lambda s: t.verify_isolated_target(plan)):
    with patch('soco.services.requests.post', return_value=response(payload=xml('missing'))) as post:
        try: check(speaker)
        except t.TopologyIncomplete: pass
        else: raise AssertionError('missing coordinator authorized safety check')
        assert speaker.group.coordinator is None  # Actual SDK parser result.
        assert post.call_count == 1
        assert post.call_args.kwargs['headers']['SOAPACTION'].endswith('#GetZoneGroupState')
''')

    def test_builtin_connection_and_distinct_socket_timeout_are_transient(self):
        self.sdk(r'''
# On Python 3.9 socket.timeout was distinct from builtins.TimeoutError.
class LegacySocketTimeout(OSError): pass
with patch.object(t.socket, 'timeout', LegacySocketTimeout):
    for error in (builtins.ConnectionError('connection'), LegacySocketTimeout('timeout'),
                  socket.timeout('timeout'), requests.exceptions.ConnectionError('connection')):
        assert t._temporary_observation_error(error), type(error)
for error in (OSError('disk'), ValueError('protocol'),
              requests.exceptions.InvalidURL('bad URL')):
    assert not t._temporary_observation_error(error), type(error)
''')

    def test_transient_http_and_truncated_response_retry_reads_only(self):
        self.sdk(r'''
for first in [requests.exceptions.ChunkedEncodingError('truncated'),
              response(502), response(503), response(504)]:
    clock = Clock()
    with patch.object(t.time, 'monotonic', lambda: clock.now), \
         patch.object(t.time, 'sleep', clock.sleep), \
         patch('soco.services.requests.post', side_effect=[first, response()]) as post:
        t._wait_for_topology(speaker, 'target', frozenset({'target'}), 2)
        assert post.call_count == 2
        assert all(c.kwargs['headers']['SOAPACTION'].endswith('#GetZoneGroupState')
                   for c in post.call_args_list)
for status in (401, 402, 500, 501):
    with patch('soco.services.requests.post', return_value=response(status)) as post:
        try: t._wait_for_topology(speaker, 'target', frozenset({'target'}), 2)
        except t.TopologyError: pass
        else: raise AssertionError(status)
        assert post.call_count == 1
''')
