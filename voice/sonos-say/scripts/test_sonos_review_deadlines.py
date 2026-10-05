"""Offline regressions for cleanup phase budgets and identity cancellation."""
import unittest
import http.client
import http.server
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import test_sonos_say  # Install the suite's offline SoCo stub first.
import sonos_say as say
import sonos_topology as topology
from sonos_test_support import FakeSpeaker, loopback_server


class TestCleanupIdentityBudget(unittest.TestCase):
    def test_identity_fallback_uses_remaining_local_budget(self):
        speaker = FakeSpeaker()
        plan = topology.validate_announcement_target(speaker)
        now = [0.0]
        local_end = say.CLEANUP_BUDGET_SECONDS - say.GROUP_RESTORE_RESERVE_SECONDS

        def unavailable(*args):
            now[0] = local_end - 0.75
            raise topology.TopologyUnavailable()

        def late_body(*args):
            now[0] = local_end + 0.01
            return (b'<root xmlns="urn:schemas-upnp-org:device-1-0">'
                    b'<device><UDN>uuid:target</UDN></device></root>')

        with patch.object(topology.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(say, 'verify_isolated_target', side_effect=unavailable), \
                patch.object(say, 'restore_target_group') as regroup, \
                patch.object(topology.http.client, 'HTTPConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.side_effect = late_body
            failed, interrupt = say._restore_after_announcement(
                speaker, None, False, 19, plan, True)
        self.assertLessEqual(connection.call_args.kwargs['timeout'], 0.75)
        self.assertTrue(failed)
        self.assertIsNone(interrupt)
        self.assertEqual(speaker.volume, 30, 'late identity must not authorize writes')
        regroup.assert_called_once_with(plan)


class TestPhaseBudgets(unittest.TestCase):
    def test_retry_never_receives_more_than_remaining_phase_budget(self):
        now, calls = [0.0], []

        def unavailable(timeout):
            calls.append(timeout)
            raise topology.TopologyUnavailable()

        with patch.object(topology.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(topology.time, 'sleep',
                             side_effect=lambda dt: now.__setitem__(0, now[0] + dt)):
            with self.assertRaises(topology.TopologyUnavailable):
                topology._poll(unavailable, 0.7)
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(calls[0], 0.7)
        self.assertAlmostEqual(calls[1], 0.2)
        self.assertEqual(now[0], 0.7)

    def test_late_prejoin_sample_cannot_authorize_join(self):
        target, coordinator = FakeSpeaker(), FakeSpeaker('coord')
        target.group = coordinator.group = SimpleNamespace(
            coordinator=coordinator, members=[coordinator, target])
        plan = topology.inspect_topology(target)
        target.isolate()
        now = [0.0]
        real_inspect = topology.inspect_topology

        def late(speaker, *, timeout):
            sample = real_inspect(speaker, timeout=timeout)
            now[0] += 3
            return sample

        with patch.object(topology.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(topology, 'inspect_topology', side_effect=late):
            with self.assertRaises(topology.TopologyError):
                topology.restore_target_group(plan, timeout=5)
        target.join.assert_not_called()

    def test_topology_observation_must_finish_before_its_deadline(self):
        for timeout in (-1, 0, 0.2, 5):
            with self.subTest(timeout=timeout):
                speaker, now, calls = FakeSpeaker(), [0.0], []

                def groups(args, timeout):
                    calls.append(timeout)
                    now[0] += timeout
                    return {'ZoneGroupState': ''}

                speaker.zoneGroupTopology.GetZoneGroupState = groups
                with patch.object(topology.time, 'monotonic', side_effect=lambda: now[0]):
                    with self.assertRaises(topology.TopologyUnavailable):
                        topology.inspect_topology(speaker, timeout=timeout)
                self.assertEqual(calls, [] if timeout <= 0 else [timeout])

    def test_expired_household_read_cannot_dispatch_topology(self):
        speaker = FakeSpeaker()
        speaker._household_id = None
        now = [0.0]

        def household(args, timeout):
            now[0] = 5
            return {'CurrentHouseholdID': 'household'}

        speaker.deviceProperties.GetHouseholdID = household
        with patch.object(topology.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(speaker.zoneGroupTopology, 'GetZoneGroupState') as groups:
            with self.assertRaises(topology.TopologyUnavailable):
                topology.inspect_topology(speaker, timeout=5)
        groups.assert_not_called()

    def test_household_lookup_leaves_only_remaining_topology_budget(self):
        speaker = FakeSpeaker()
        speaker._household_id = None
        now, calls = [0.0], []

        def household(args, timeout):
            calls.append(('household', timeout))
            now[0] += 3
            return {'CurrentHouseholdID': 'household'}

        def groups(args, timeout):
            calls.append(('groups', timeout))
            return {'ZoneGroupState': ''}

        speaker.deviceProperties.GetHouseholdID = household
        speaker.zoneGroupTopology.GetZoneGroupState = groups
        with patch.object(topology.time, 'monotonic', side_effect=lambda: now[0]):
            topology.inspect_topology(speaker, timeout=5)
        self.assertEqual(calls, [('household', 5), ('groups', 2)])

    def test_coordinator_read_gets_only_remaining_prejoin_budget(self):
        target, coordinator = FakeSpeaker(), FakeSpeaker('coord')
        target.group = coordinator.group = SimpleNamespace(
            coordinator=coordinator, members=[coordinator, target])
        plan = topology.inspect_topology(target)
        now, timeouts = [0.0], []
        real_inspect = topology.inspect_topology

        def inspect(speaker, *, timeout):
            timeouts.append(timeout)
            result = real_inspect(speaker, timeout=timeout)
            now[0] += 3 if speaker is target else 1
            return result

        with patch.object(topology.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(topology, 'inspect_topology', side_effect=inspect):
            topology.restore_target_group(plan, timeout=5)
        self.assertEqual(timeouts, [5, 2])
        target.join.assert_not_called()


class TestIdentityTransportCancellation(unittest.TestCase):
    def test_connection_close_trickle_leaves_no_reader_or_open_socket(self):
        self.assert_reader_cancelled()

    def test_stalled_close_response_leaves_no_reader_or_open_socket(self):
        self.assert_reader_cancelled(trickle=False)

    def test_eof_delimited_trickle_leaves_no_reader_or_open_socket(self):
        self.assert_reader_cancelled(content_length=False)

    def test_keepalive_trickle_leaves_no_reader_or_open_socket(self):
        self.assert_reader_cancelled(connection_close=False)

    def test_trickling_headers_leave_no_reader_or_open_socket(self):
        self.assert_reader_cancelled(headers=True)

    def assert_reader_cancelled(self, *, trickle=True, content_length=True,
                                connection_close=True, headers=False):
        stop, sent, finished = (threading.Event() for _ in range(3))
        workers, sockets = [], []

        class Trickle(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_GET(self):
                try:
                    if headers:
                        self.wfile.write(b'HTTP/1.1 200 OK\r\nX-Trickle: ')
                        self.wfile.flush()
                    else:
                        self.send_response(200)
                        if connection_close:
                            self.send_header('Connection', 'close')
                        if content_length:
                            self.send_header('Content-Length', '1000')
                        self.end_headers()
                    sent.set()
                    while not stop.is_set():
                        if trickle:
                            self.wfile.write(b' ')
                            self.wfile.flush()
                        stop.wait(0.02)
                except OSError:
                    pass
                finally:
                    self.close_connection = True
                    finished.set()

            def log_message(self, format, *args):
                pass

        class Connection(http.client.HTTPConnection):
            def getresponse(self):
                sockets.append(self.sock)
                response = super().getresponse()
                if connection_close:
                    self_test.assertIsNone(self.sock, 'exercise response-owned socket')
                return response

        self_test = self
        fetch = topology._fetch_device_description

        def tracked_fetch(*args, **kwargs):
            workers.append(threading.current_thread())
            return fetch(*args, **kwargs)

        with loopback_server(Trickle) as port:
            try:
                with patch.object(topology.http.client, 'HTTPConnection',
                                  side_effect=lambda host, unused, timeout:
                                  Connection('127.0.0.1', port, timeout=timeout)), \
                        patch.object(topology, '_fetch_device_description', tracked_fetch):
                    started = time.monotonic()
                    with self.assertRaises(topology.TopologyError):
                        topology._read_device_uid(
                            SimpleNamespace(ip_address='127.0.0.1'),
                            deadline=started + 0.25)
                    elapsed = time.monotonic() - started
                self.assertTrue(sent.is_set())
                self.assertEqual(len(workers), 1)
                self.assertFalse(workers[0].is_alive(), 'timed-out reader leaked')
                self.assertEqual(sockets[0].fileno(), -1)
                self.assertLess(elapsed, 1)
            finally:
                stop.set()
                self.assertTrue(finished.wait(2))
                for worker in workers:
                    worker.join(2)
                    self.assertFalse(worker.is_alive(), 'test left a reader behind')
