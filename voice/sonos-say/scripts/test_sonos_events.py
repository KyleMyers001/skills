"""Attempt-correlated AVTransport evidence; real SDK boundary in subprocesses."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import test_sonos_say  # installs only this process's SoCo fixtures
import sonos_say as say
from sonos_test_support import FakeSpeaker


class TestTransportEventEvidence(unittest.TestCase):
    def recorder(self):
        # The production queue accepts SDK Event-shaped objects, not raw XML.
        return say._TransportEventQueue('http://example.invalid/attempt')

    def event(self, recorder, seq, state, uri='http://example.invalid/attempt', sid='attempt', timestamp=None):
        recorder.put(SimpleNamespace(seq=str(seq), sid=sid, timestamp=timestamp if timestamp is not None else time.time(),
            variables={'transport_state': state, 'av_transport_uri': uri}))

    def arm(self, recorder):
        recorder.arm('attempt')

    def test_polling_misses_playing_but_ordered_events_prove_completion(self):
        q = self.recorder(); self.arm(q)
        self.event(q, 1, 'PLAYING'); self.event(q, 2, 'STOPPED')
        sp = FakeSpeaker(); sp.state = 'STOPPED'
        self.assertTrue(say.wait_for_playback(sp, 1, events=q))

    def test_stopped_alone_and_initial_or_prior_uri_playing_never_succeed(self):
        for prior in ('initial', 'prior-uri', 'other-sid', 'duplicate', 'stale'):
            with self.subTest(prior=prior):
                q = self.recorder()
                if prior == 'initial': self.event(q, 0, 'PLAYING')
                self.arm(q)
                if prior == 'prior-uri': self.event(q, 1, 'PLAYING', uri='prior')
                if prior == 'other-sid': self.event(q, 1, 'PLAYING', sid='other')
                if prior == 'duplicate':
                    self.event(q, 1, 'STOPPED'); self.event(q, 1, 'PLAYING')
                if prior == 'stale': self.event(q, 0, 'PLAYING')
                self.event(q, 2, 'STOPPED')
                sp = FakeSpeaker()
                now = [0.0]
                with patch.object(say.time, 'monotonic', side_effect=lambda: now[0]), \
                        patch.object(say.time, 'sleep', side_effect=lambda dt: now.__setitem__(0, now[0]+dt)):
                    self.assertFalse(say.wait_for_playback(sp, 1, events=q))

    def test_pause_loss_malformed_and_overflow_fail_closed(self):
        for bad in ('pause', 'gap', 'malformed', 'overflow'):
            with self.subTest(bad=bad):
                q = self.recorder(); self.arm(q)
                self.event(q, 1, 'PLAYING')
                if bad == 'pause': self.event(q, 2, 'PAUSED_PLAYBACK')
                if bad == 'gap': self.event(q, 3, 'STOPPED')
                if bad == 'malformed': self.event(q, 'invalid', 'STOPPED')
                if bad == 'overflow':
                    for n in range(2, 200): self.event(q, n, 'PLAYING')
                self.event(q, 200, 'STOPPED')
                self.assertFalse(say.wait_for_playback(FakeSpeaker(), 1, events=q))

    def test_overall_deadline_includes_blocking_play_rpc(self):
        q = self.recorder()
        now = [0.0]
        with patch.object(say.time, 'monotonic', side_effect=lambda: now[0]):
            self.arm(q)
            now[0] = 2
            self.event(q, 1, 'PLAYING'); self.event(q, 2, 'STOPPED')
            self.assertFalse(say.wait_for_playback(FakeSpeaker(), 1, events=q))

    def test_soap_start_after_start_deadline_cannot_be_success(self):
        q = self.recorder()
        now = [0.0]
        with patch.object(say.time, 'monotonic', side_effect=lambda: now[0]):
            self.arm(q)
            now[0] = 9
            sp = FakeSpeaker(); sp.uri = q.uri; sp.state = 'PLAYING'
            # Finite parent clock: any erroneous polling must reach overall expiry.
            def advance(dt): now[0] += dt
            with patch.object(say.time, 'sleep', side_effect=advance):
                self.assertFalse(say.wait_for_playback(sp, 30, events=q))
            self.assertEqual(now[0], 9)

    def test_late_uri_readback_cannot_extend_deadlines(self):
        for prior in (False, True):
            with self.subTest(prior=prior):
                q = self.recorder(); now = [0.0]
                with patch.object(say.time, 'monotonic', side_effect=lambda: now[0]):
                    self.arm(q)
                    if prior: self.event(q, 1, 'PLAYING')
                    sp = FakeSpeaker(); sp.uri = q.uri
                    sp.state = 'STOPPED' if prior else 'PLAYING'
                    def slow_media(args):
                        now[0] = 40
                        return {'CurrentURI':q.uri}
                    sp.avTransport.GetMediaInfo = slow_media
                    self.assertFalse(say.wait_for_playback(sp, 30, events=q))

    def test_lost_subscription_cannot_be_soap_success(self):
        q = self.recorder(); self.arm(q)
        q.failed = True
        speaker = FakeSpeaker()
        speaker.get_current_transport_info = MagicMock(side_effect=[
            {'current_transport_state': 'PLAYING'}, {'current_transport_state': 'STOPPED'}])
        self.assertFalse(say.wait_for_playback(speaker, 1, events=q))

    def test_unsupported_backend_is_refused(self):
        with patch.object(say, '_threaded_events_backend', side_effect=RuntimeError('unsupported')):
            with self.assertRaises(RuntimeError):
                with say._TransportEvents(FakeSpeaker(), 'http://example.invalid/attempt'):
                    self.fail('unsupported backend was accepted')


_SDK = r'''
import http.client, json, os, socket, threading, time, urllib.request
from types import SimpleNamespace
from unittest.mock import patch
import soco, soco.config, soco.events as events
from soco.events_base import Event
import sonos_say as say
assert soco.__version__ == '0.31.2'
soco.config.EVENT_LISTENER_IP = '127.0.0.1'
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
soco.config.EVENT_LISTENER_PORT = port
events.event_listener.requested_port_number = port
state = SimpleNamespace(add_subscription=lambda s: None, remove_subscription=lambda s: None)
from soco.services import AVTransport
# Real SDK service/subscription/NOTIFY, but no device queries or physical LAN.
sp = SimpleNamespace(ip_address='127.0.0.1', zone_group_state=state)
sp.avTransport = AVTransport(sp)
calls = []
def network(method, url, headers, **kw):
    calls.append(method)
    if os.environ.get('FAIL_SUBSCRIBE') and method == 'SUBSCRIBE':
        import requests
        raise requests.exceptions.ConnectionError('fixed fake network failure')
    sid = 'uuid:test-' + str(calls.count('SUBSCRIBE'))
    return SimpleNamespace(status_code=200, headers={'sid': sid, 'timeout': 'Second-600'},
                           raise_for_status=lambda: None)
with patch.object(events.requests, 'request', side_effect=network):
    outsider = None
    if os.environ.get('RETAIN_SUBSCRIPTION'):
        outsider = events.Subscription(sp.avTransport)
        outsider.subscribe(requested_timeout=600)
    recorder = say._TransportEvents(sp, 'http://example.invalid/attempt')
    original_wake = urllib.request.urlopen
    def force_wake_race(url, *, timeout):
        assert timeout == 1
        thread = recorder.listener._listener_thread
        # Finish accept() after the stop flag was set, before the SDK dummy GET.
        sock = socket.create_connection(recorder.listener.address, timeout=1)
        sock.close()
        thread.join(1)
        assert not thread.is_alive()
        return original_wake(url, timeout=timeout)
    wake_patch = patch.object(urllib.request, 'urlopen', force_wake_race)
    if os.environ.get('FORCE_WAKE_RACE'): wake_patch.start()
    try:
        with recorder:
            sub = recorder.subscription
            address = recorder.listener.address
            listener = recorder.listener._listener_thread
            recorder.arm()
            # Real loopback NOTIFY and SDK XML parsing/delivery.
            for seq, transport in ((1, 'PLAYING'), (2, 'STOPPED')):
                import html
                change = '<Event xmlns="urn:schemas-upnp-org:metadata-1-0/AVT/"><InstanceID val="0"><TransportState val="'+transport+'"/><AVTransportURI val="http://example.invalid/attempt"/></InstanceID></Event>'
                body = '<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0"><e:property><LastChange>'+html.escape(change)+'</LastChange></e:property></e:propertyset>'
                conn = http.client.HTTPConnection(*address, timeout=2)
                conn.request('NOTIFY', '/', body=body, headers={'SEQ':str(seq), 'SID': sub.sid})
                resp=conn.getresponse(); assert resp.status == 200; resp.read(); conn.close()
            assert recorder.queue.observe(8, 30) == (True, True)
    except Exception:
        if not os.environ.get('FAIL_SUBSCRIBE'): raise
        listener = recorder.listener._listener_thread
        address = listener.server.server_address
    if os.environ.get('FORCE_WAKE_RACE'): wake_patch.stop()
    assert not recorder.listener.is_running and not listener.is_alive()
    if outsider is not None:
        global_thread = events.event_listener._listener_thread
        assert events.event_listener.is_running and global_thread.is_alive()
        assert events.subscriptions_map.count == 1 and outsider.is_subscribed
        # The test owns this outsider, not the announcement. Bound SDK's known
        # unbounded wake-up only for its test teardown.
        import urllib.request
        with patch.object(events, 'urlopen', side_effect=lambda url:
                          urllib.request.urlopen(url, timeout=1)):
            outsider.unsubscribe()
        global_thread.server.server_close()
        assert not global_thread.is_alive()
    assert not events.event_listener.is_running
    assert not listener.is_alive()
    assert events.subscriptions_map.count == 0
    assert listener.server.fileno() == -1, 'SDK stop alone leaks listening socket'
print(json.dumps({'calls':calls, 'closed':True}))
'''


class TestInstalledSDKEvents(unittest.TestCase):
    def test_threaded_sdk_notify_and_subscription_failure_close_listener(self):
        for fail, retain, race in ((False, False, False), (True, False, False),
                                   (False, True, False), (False, False, True)):
            with self.subTest(fail=fail, retain=retain, race=race):
                env = {'PATH': os.defpath, 'PYTHONPATH': str(Path(__file__).parent)}
                if fail: env['FAIL_SUBSCRIBE'] = '1'
                if retain: env['RETAIN_SUBSCRIPTION'] = '1'
                if race: env['FORCE_WAKE_RACE'] = '1'
                result = subprocess.run([sys.executable, '-c', _SDK], env=env,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                evidence = json.loads(result.stdout)
                self.assertTrue(evidence['closed'])
                expected = (['SUBSCRIBE'] if fail else
                            ['SUBSCRIBE', 'SUBSCRIBE', 'UNSUBSCRIBE', 'UNSUBSCRIBE'] if retain
                            else ['SUBSCRIBE', 'UNSUBSCRIBE'])
                self.assertEqual(evidence['calls'], expected)
