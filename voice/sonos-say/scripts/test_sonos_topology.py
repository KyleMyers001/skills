"""Behavior tests for strict temporary Sonos target isolation."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import sonos_topology as topology
from sonos_test_support import FakeSpeaker, make_bonded_room


def _speaker(uid: str) -> MagicMock:
    speaker = MagicMock()
    speaker.uid = speaker._uid = uid
    speaker._is_satellite = False
    speaker._has_satellites = False
    speaker._visible = True
    speaker.zone_group_state.has_subscriptions = False
    speaker.zone_group_state.processed_count = 0
    return speaker


def _set_group(
    speaker: MagicMock,
    coordinator: MagicMock,
    members: list[MagicMock],
) -> None:
    group = SimpleNamespace(
        coordinator=coordinator,
        members=members,
    )
    speaker.group = group
    for member in members:
        member.group = group
        member.zone_group_state.visible_zones = {
            visible for visible in members if visible._visible
        }


def _isolate(
    target: MagicMock,
    coordinator: MagicMock,
) -> None:
    _set_group(coordinator, coordinator, [coordinator])
    _set_group(target, target, [target])


class TestTopologyLifecycle(unittest.TestCase):

    def test_standalone_is_not_mutated(self):
        target = _speaker("target")
        _set_group(target, target, [target])

        plan = topology.inspect_topology(target)
        topology.detach_target(plan)
        topology.restore_target_group(plan)

        self.assertFalse(plan.detach)
        target.unjoin.assert_not_called()
        target.join.assert_not_called()

    def test_coordinator_is_not_mutated(self):
        target = _speaker("target")
        member = _speaker("member")
        _set_group(target, target, [target, member])

        plan = topology.inspect_topology(target)
        topology.detach_target(plan)
        topology.restore_target_group(plan)

        self.assertFalse(plan.detach)
        target.unjoin.assert_not_called()
        target.join.assert_not_called()

    def test_grouped_coordinator_is_not_eligible_for_announcement(self):
        target, member = _speaker("target"), _speaker("member")
        _set_group(target, target, [target, member])
        with self.assertRaises(topology.TopologyError):
            topology.validate_announcement_target(target)
        target.unjoin.assert_not_called()
        target.play_uri.assert_not_called()

    def test_bonded_hidden_or_unknown_target_is_not_eligible(self):
        for name, value in (("_is_satellite", True), ("_has_satellites", True),
                            ("_visible", False), ("_has_satellites", None)):
            with self.subTest(name=name, value=value):
                target = _speaker("target")
                _set_group(target, target, [target])
                setattr(target, name, value)
                if name == '_visible':
                    target.zone_group_state.visible_zones = set()
                with self.assertRaises(topology.TopologyError):
                    topology.validate_announcement_target(target)

    def test_isolation_recheck_rejects_regrouped_target(self):
        target, member = _speaker("target"), _speaker("member")
        _set_group(target, target, [target])
        plan = topology.validate_announcement_target(target)
        _set_group(target, target, [target, member])
        with self.assertRaises(topology.TopologyError):
            topology.verify_isolated_target(plan)

    def test_topology_change_before_detach_does_not_mutate(self):
        target, coord, other = (_speaker(uid) for uid in ("target", "coord", "other"))
        _set_group(target, coord, [coord, target])
        plan = topology.validate_announcement_target(target)
        _set_group(target, other, [other, target])
        with self.assertRaises(topology.TopologyError):
            topology.detach_target(plan)
        target.unjoin.assert_not_called()

    def test_cached_uid_is_not_live_identity_evidence(self):
        target = _speaker('target')
        _set_group(target, target, [target])
        plan = topology.validate_announcement_target(target)
        xml = (b'<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
               b'<UDN>uuid:replacement</UDN></device></root>')
        with patch('http.client.HTTPConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = xml
            with self.assertRaises(topology.TopologyError):
                topology.verify_target_identity(plan)
        self.assertEqual(target.uid, 'target')

    def test_member_detaches_and_rejoins_exact_coordinator(self):
        target = _speaker("target")
        coordinator = _speaker("coordinator")
        _set_group(target, coordinator, [coordinator, target])
        plan = topology.inspect_topology(target)

        target.unjoin.side_effect = lambda: _isolate(target, coordinator)
        target.join.side_effect = lambda prior: _set_group(
            target, prior, [prior, target]
        )
        topology.detach_target(plan)
        topology.restore_target_group(plan)

        target.unjoin.assert_called_once_with()
        target.join.assert_called_once_with(coordinator)

    def test_three_member_group_restores_through_grouped_coordinator(self):
        target, coordinator, member = (
            _speaker(uid) for uid in ("target", "coordinator", "member")
        )
        original = [coordinator, member, target]
        _set_group(target, coordinator, original)
        plan = topology.validate_announcement_target(target)

        def isolate():
            _set_group(coordinator, coordinator, [coordinator, member])
            _set_group(target, target, [target])

        target.unjoin.side_effect = isolate
        target.join.side_effect = lambda prior: _set_group(target, prior, original)
        topology.detach_target(plan)
        topology.verify_isolated_target(plan)
        topology.restore_target_group(plan)
        target.join.assert_called_once_with(coordinator)
        self.assertEqual(topology.inspect_topology(target).member_uids,
                         frozenset({"target", "coordinator", "member"}))

    def test_duplicate_member_identity_fails_closed(self):
        target = _speaker("target")
        duplicate = _speaker("target")
        _set_group(target, target, [target, duplicate])

        with self.assertRaises(topology.TopologyError):
            topology.inspect_topology(target)
        target.unjoin.assert_not_called()

    def test_restore_failure_is_reported(self):
        target = _speaker("target")
        coordinator = _speaker("coordinator")
        _set_group(target, coordinator, [coordinator, target])
        plan = topology.inspect_topology(target)
        target.unjoin.side_effect = lambda: _isolate(target, coordinator)
        target.join.side_effect = RuntimeError("offline")

        topology.detach_target(plan)
        with self.assertRaises(topology.TopologyError):
            topology.restore_target_group(plan)

    def test_detachment_verification_failure_is_reported(self):
        target = _speaker("target")
        coordinator = _speaker("coordinator")
        _set_group(target, coordinator, [coordinator, target])
        plan = topology.inspect_topology(target)

        with self.assertRaises(topology.TopologyError):
            topology.detach_target(plan, timeout=0)

    def test_detachment_waits_for_async_convergence(self):
        target = _speaker("target")
        coordinator = _speaker("coordinator")
        _set_group(target, coordinator, [coordinator, target])
        plan = topology.inspect_topology(target)

        with patch(
            "sonos_topology.time.sleep",
            side_effect=lambda delay: _isolate(target, coordinator),
        ) as sleep:
            topology.detach_target(plan, timeout=1)

        sleep.assert_called_once_with(topology.TOPOLOGY_POLL_SECONDS)

    def test_restore_rejects_topology_change_during_playback(self):
        target = _speaker("target")
        coordinator = _speaker("coordinator")
        intruder = _speaker("intruder")
        _set_group(target, coordinator, [coordinator, target])
        plan = topology.inspect_topology(target)
        _isolate(target, coordinator)
        _set_group(coordinator, coordinator, [coordinator, intruder])

        with self.assertRaises(topology.TopologyError):
            topology.restore_target_group(plan, timeout=0)

        target.join.assert_not_called()

    def test_restore_waits_for_async_exact_membership(self):
        target = _speaker("target")
        coordinator = _speaker("coordinator")
        _set_group(target, coordinator, [coordinator, target])
        plan = topology.inspect_topology(target)
        _isolate(target, coordinator)

        def _converge(delay):
            _set_group(target, coordinator, [coordinator, target])

        with patch(
            "sonos_topology.time.sleep",
            side_effect=_converge,
        ) as sleep:
            topology.restore_target_group(plan, timeout=1)

        sleep.assert_called_once_with(topology.TOPOLOGY_POLL_SECONDS)
        target.join.assert_called_once_with(coordinator)

    def test_restore_rejects_incomplete_original_membership(self):
        target = _speaker("target")
        coordinator = _speaker("coordinator")
        member = _speaker("member")
        _set_group(target, coordinator, [coordinator, member, target])
        plan = topology.inspect_topology(target)
        _set_group(coordinator, coordinator, [coordinator, member])
        _set_group(target, target, [target])
        target.join.side_effect = lambda prior: _set_group(
            target,
            prior,
            [prior, target],
        )

        with self.assertRaises(topology.TopologyError):
            topology.restore_target_group(plan, timeout=0)


class TestBondedRoomEligibility(unittest.TestCase):
    def test_primary_and_owned_satellites_are_one_room(self):
        primary, satellites = make_bonded_room()

        plan = topology.validate_announcement_target(primary)
        topology.verify_isolated_target(plan)
        topology.detach_target(plan)
        topology.restore_target_group(plan)

        self.assertFalse(plan.detach)
        self.assertEqual(plan.room_member_uids,
                         frozenset(member.uid for member in (primary, *satellites)))
        self.assertEqual(
            {member.parent_uid for member in plan.member_topology
             if member.satellite}, {primary.uid},
        )
        for member in (primary, *satellites):
            member.unjoin.assert_not_called()
            member.join.assert_not_called()
            member.play_uri.assert_not_called()

    def test_identical_room_names_do_not_prove_bonding(self):
        primary, satellites = make_bonded_room()
        other = FakeSpeaker('another-room')
        primary.group.members.append(other)
        other.group = primary.group
        for member in (primary, *satellites, other):
            member.player_name = 'Same Room'
        with self.assertRaises(topology.TopologyError):
            topology.validate_announcement_target(primary)


    def test_unknown_or_contradictory_bonding_fails_closed(self):
        cases = ('missing-parent', 'foreign-parent', 'unknown-satellite',
                 'missing-satellites', 'duplicate',
                 'unknown-primary', 'contradictory-primary')
        for case in cases:
            with self.subTest(case=case):
                primary, satellites = make_bonded_room()
                if case == 'missing-parent':
                    del satellites[0]._satellite_parent
                elif case == 'foreign-parent':
                    satellites[0]._satellite_parent = FakeSpeaker('foreign')
                elif case == 'unknown-satellite':
                    satellites[0]._is_satellite = None
                elif case == 'missing-satellites':
                    primary.group.members = [primary]
                elif case == 'duplicate':
                    satellites[0]._uid = satellites[1].uid
                elif case == 'unknown-primary':
                    primary._has_satellites = None
                elif case == 'contradictory-primary':
                    primary._has_satellites = False
                with self.assertRaises(topology.TopologyError):
                    topology.validate_announcement_target(primary)
                primary.unjoin.assert_not_called()
                primary.play_uri.assert_not_called()

    def test_direct_satellite_and_hidden_primary_are_refused(self):
        primary, satellites = make_bonded_room()
        with self.assertRaises(topology.TopologyError):
            topology.validate_announcement_target(satellites[0])
        primary._visible = False
        with self.assertRaises(topology.TopologyError):
            topology.validate_announcement_target(primary)

    def test_satellite_primary_only_flags_do_not_determine_ownership(self):
        primary, satellites = make_bonded_room()
        satellites[0]._has_satellites = None
        satellites[1]._has_satellites = True
        topology.validate_announcement_target(primary)

    def test_bonded_member_of_multiroom_group_is_never_detached(self):
        primary, satellites = make_bonded_room()
        coordinator = FakeSpeaker('other-room')
        primary.group.members.append(coordinator)
        primary.group.coordinator = coordinator
        plan = topology.inspect_topology(primary)
        with self.assertRaises(topology.TopologyError):
            topology.detach_target(plan)
        primary.unjoin.assert_not_called()
        primary.join.assert_not_called()

    def test_exact_membership_and_parent_links_survive_mutable_sdk_objects(self):
        for change in ('parent', 'remove', 'add', 'coordinator'):
            with self.subTest(change=change):
                primary, satellites = make_bonded_room()
                plan = topology.validate_announcement_target(primary)
                saved_roles = plan.member_topology
                if change == 'parent':
                    satellites[0]._satellite_parent = FakeSpeaker('foreign')
                elif change == 'remove':
                    primary.group.members.remove(satellites[0])
                elif change == 'add':
                    extra = FakeSpeaker('new-satellite')
                    extra._visible, extra._is_satellite = False, True
                    extra._satellite_parent = primary
                    primary.group.members.append(extra)
                else:
                    primary.group.coordinator = satellites[0]
                self.assertEqual(plan.member_topology, saved_roles)
                self.assertEqual(len(plan.room_member_uids), 3)
                with self.assertRaises(topology.TopologyError):
                    topology.verify_isolated_target(plan)

    def test_raw_inspection_keeps_unknown_eligibility_flags(self):
        primary, satellites = make_bonded_room()
        primary._has_satellites = None
        plan = topology.inspect_topology(primary)
        self.assertEqual(len(plan.member_uids), 3)
        self.assertIsNone(next(member for member in plan.member_topology
                               if member.uid == primary.uid).has_satellites)
        with self.assertRaises(topology.TopologyError):
            topology.validate_announcement_target(primary)

    def test_public_role_getters_are_never_read(self):
        primary, satellites = make_bonded_room()
        with contextlib.ExitStack() as stack:
            for role in ('uid', 'is_visible', 'is_satellite', 'has_satellites'):
                stack.enter_context(patch.object(
                    FakeSpeaker, role, new_callable=PropertyMock,
                    side_effect=RuntimeError('satellite unreachable'),
                ))
            plan = topology.validate_announcement_target(primary)
        self.assertEqual(len(plan.room_member_uids), 3)

    def test_asynchronous_state_change_during_copy_fails_closed(self):
        primary, satellites = make_bonded_room()
        state = primary.zone_group_state
        def newer_sample():
            state.processed_count += 1
            return {primary}
        with patch.object(type(state), 'visible_zones',
                          new_callable=PropertyMock, side_effect=newer_sample):
            with self.assertRaises(topology.TopologyError):
                topology.validate_announcement_target(primary)

    def test_fake_uid_and_roles_are_read_only(self):
        speaker = FakeSpeaker()
        for role in ('uid', 'is_visible', 'is_satellite', 'has_satellites'):
            with self.subTest(role=role), self.assertRaises(AttributeError):
                setattr(speaker, role, None)

    def test_missing_processed_visibility_fails_closed(self):
        primary, satellites = make_bonded_room()
        with patch.object(type(primary.zone_group_state), 'visible_zones',
                          new_callable=PropertyMock, return_value=None):
            with self.assertRaises(topology.TopologyError):
                topology.validate_announcement_target(primary)
        primary.play_uri.assert_not_called()

    def test_missing_observation_identity_is_refused(self):
        primary, satellites = make_bonded_room()
        primary.zone_group_state.processed_count = None
        with self.assertRaises(topology.TopologyError):
            topology.validate_announcement_target(primary)


class _Household:
    """SoCo's shared five-second polling cache, independent of live state."""

    def __init__(self):
        self.now = 0.0
        self.live = {}
        self.cached = {}
        self.cache_until = -1.0
        self.has_subscriptions = False
        self.samples = 0
        self.processed_count = 0
        self.on_sleep = None

    def clear_cache(self):
        self.cache_until = -1.0

    def group(self, uid):
        if not self.has_subscriptions and self.now >= self.cache_until:
            self.cached = dict(self.live)
            self.cache_until = self.now + 5
            self.samples += 1
            self.processed_count += 1
        return self.cached[uid]

    @property
    def visible_zones(self):
        return {member for group in self.cached.values()
                for member in group.members}

    def group_members(self, coordinator, members):
        group = SimpleNamespace(coordinator=coordinator, members=members)
        for member in members:
            self.live[member.uid] = group

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


class _CachedSpeaker:
    _is_satellite = False
    _has_satellites = False

    def __init__(self, uid, household):
        self._uid = uid
        self.zone_group_state = household
        self.unjoin = MagicMock()
        self.join = MagicMock()

    @property
    def uid(self):
        return self._uid

    @property
    def group(self):
        return self.zone_group_state.group(self.uid)


class TestFreshTopology(unittest.TestCase):
    def setUp(self):
        self.household = _Household()
        self.target = _CachedSpeaker("target", self.household)
        self.coord = _CachedSpeaker("coord", self.household)
        self.household.group_members(self.coord, [self.coord, self.target])
        clock = patch("sonos_topology.time.monotonic",
                      side_effect=lambda: self.household.now)
        sleep = patch("sonos_topology.time.sleep", self.household.sleep)
        clock.start()
        sleep.start()
        self.addCleanup(clock.stop)
        self.addCleanup(sleep.stop)

    def isolate(self):
        self.household.group_members(self.coord, [self.coord])
        self.household.group_members(self.target, [self.target])

    def test_fake_really_retains_stale_data_until_invalidated(self):
        original = self.target.group
        self.isolate()
        self.household.now += 0.1
        self.assertIs(self.target.group, original)
        self.household.clear_cache()
        self.assertIs(self.target.group.coordinator, self.target)

    def test_delayed_detach_and_rejoin_converge_before_cache_expiry(self):
        plan = topology.validate_announcement_target(self.target)
        # RPC invalidates once, like SoCo. First observation remains stale;
        # later polling must invalidate again to see delayed publication.
        self.target.unjoin.side_effect = self.household.clear_cache
        self.household.on_sleep = self.isolate
        topology.detach_target(plan)
        self.assertLess(self.household.now, 5)
        self.target.join.side_effect = lambda prior: self.household.clear_cache()
        self.household.on_sleep = lambda: self.household.group_members(
            self.coord, [self.coord, self.target])
        topology.restore_target_group(plan)
        self.assertLess(self.household.now, 5)
        self.target.join.assert_called_once_with(self.coord)
        self.assertEqual(topology.inspect_topology(self.target).member_uids,
                         frozenset({"target", "coord"}))

    def test_external_regrouping_is_observed_before_playback(self):
        self.isolate()
        plan = topology.validate_announcement_target(self.target)
        self.household.group_members(self.coord, [self.coord, self.target])
        with self.assertRaises(topology.TopologyError):
            topology.verify_isolated_target(plan)

    def test_coordinator_restoration_check_does_not_accept_stale_membership(self):
        plan = topology.validate_announcement_target(self.target)
        self.isolate()
        self.household.clear_cache()
        topology.inspect_topology(self.coord)  # prime singleton coordinator view
        intruder = _CachedSpeaker("intruder", self.household)
        self.household.group_members(self.coord, [self.coord, intruder])
        with self.assertRaises(topology.TopologyError):
            topology.restore_target_group(plan, timeout=0)
        self.target.join.assert_not_called()

    def test_active_subscription_fails_closed(self):
        self.target.group  # prime an apparently valid view
        self.household.has_subscriptions = True
        with self.assertRaises(topology.TopologyError):
            topology.validate_announcement_target(self.target)
        self.target.unjoin.assert_not_called()

    def test_cache_clear_and_network_read_errors_fail_closed(self):
        for method in ("clear_cache", "group"):
            with self.subTest(method=method):
                with patch.object(self.household, method,
                                  side_effect=RuntimeError("unavailable")):
                    with self.assertRaises(topology.TopologyError):
                        topology.inspect_topology(self.target)

    def test_eligibility_uses_the_same_sampled_group(self):
        self.target.group  # Populate parsed visibility before overriding the getter.
        with patch.object(type(self.target), "group",
                          new_callable=PropertyMock) as group:
            group.side_effect = [self.household.live["target"],
                                 RuntimeError("second read")]
            topology.validate_announcement_target(self.target)
            self.assertEqual(group.call_count, 1)

    def test_before_unjoin_only_runs_after_fresh_validation(self):
        plan = topology.validate_announcement_target(self.target)
        self.isolate()  # another controller changed it
        callback = MagicMock()
        with self.assertRaises(topology.TopologyError):
            topology.detach_target(plan, before_unjoin=callback)
        callback.assert_not_called()
        self.target.unjoin.assert_not_called()

    def test_mutation_callback_runs_before_rpc_that_mutates_then_raises(self):
        plan = topology.validate_announcement_target(self.target)
        attempted = []

        def unjoin():
            self.assertEqual(attempted, [True])
            self.isolate()
            raise TimeoutError("RPC timed out after mutation")

        self.target.unjoin.side_effect = unjoin
        with self.assertRaises(topology.TopologyError):
            topology.detach_target(plan, before_unjoin=lambda: attempted.append(True))
        self.assertIs(self.household.live["target"].coordinator, self.target)


class TestIdentityDescription(unittest.TestCase):
    def setUp(self):
        self.target = FakeSpeaker()
        self.plan = topology.validate_announcement_target(self.target)
        connection = patch('http.client.HTTPConnection')
        self.connection = connection.start()
        self.addCleanup(connection.stop)
        self.response = self.connection.return_value.getresponse.return_value
        self.response.status = 200

    def description(self, udn):
        return (f'<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
                f'<UDN>{udn}</UDN></device></root>').encode()

    def test_live_description_matches_without_reusing_cached_uid(self):
        self.response.read.return_value = self.description('uuid:target')
        topology.verify_target_identity(self.plan)
        self.connection.assert_called_once_with('192.0.2.2', 1400, timeout=5.0)
        self.connection.return_value.request.assert_called_once_with(
            'GET', '/xml/device_description.xml')
        self.response.read.assert_called_once_with(topology.DEVICE_DESCRIPTION_LIMIT + 1)
        self.connection.return_value.close.assert_called_once()

    def test_missing_malformed_and_replacement_udns_fail_closed(self):
        bodies = [self.description(value) for value in
                  ('uuid:replacement', 'target', 'uuid:', 'uuid: target')]
        bodies += [b'<broken', b'<root/>',
                   b'x' * (topology.DEVICE_DESCRIPTION_LIMIT + 1)]
        for body in bodies:
            with self.subTest(body=body[:40]):
                self.response.read.return_value = body
                with self.assertRaises(topology.TopologyError):
                    topology.verify_target_identity(self.plan)
        self.assertEqual(self.target.uid, 'target')
        self.assertEqual(self.connection.return_value.close.call_count, len(bodies))

    def test_redirects_errors_and_unavailable_device_fail_closed(self):
        for status in (301, 302, 404, 500):
            with self.subTest(status=status):
                self.response.status = status
                with self.assertRaises(topology.TopologyError):
                    topology.verify_target_identity(self.plan)
        self.response.read.assert_not_called()
        self.connection.return_value.getresponse.side_effect = TimeoutError()
        with self.assertRaises(topology.TopologyError):
            topology.verify_target_identity(self.plan)
        self.assertEqual(self.connection.return_value.close.call_count, 5)


class TestTopologyReadRetries(unittest.TestCase):
    def setUp(self):
        self.target, self.coord = FakeSpeaker(), FakeSpeaker('coord')
        self.now = 0.0
        clock = patch.object(topology.time, 'monotonic', side_effect=lambda: self.now)
        sleep = patch.object(topology.time, 'sleep', side_effect=self.advance)
        clock.start()
        self.sleep = sleep.start()
        self.addCleanup(clock.stop)
        self.addCleanup(sleep.stop)

    def advance(self, seconds):
        self.now += seconds

    def grouped_plan(self):
        self.target.group = self.coord.group = SimpleNamespace(
            coordinator=self.coord, members=[self.coord, self.target])
        plan = topology.validate_announcement_target(self.target)
        self.target.isolate()
        return plan

    def test_isolation_retries_unavailable_sample_and_then_succeeds(self):
        plan = topology.validate_announcement_target(self.target)
        observed = topology.inspect_topology(self.target)
        with patch.object(topology, 'inspect_topology', side_effect=[
            topology.TopologyUnavailable(), observed,
        ]) as inspect:
            topology.verify_isolated_target(plan, timeout=1)
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(self.now, 0.5)

    def test_isolation_unavailable_is_bounded_and_unsafe_is_not_retried(self):
        plan = topology.validate_announcement_target(self.target)
        with patch.object(topology, 'inspect_topology',
                          side_effect=topology.TopologyUnavailable()):
            with self.assertRaises(topology.TopologyError):
                topology.verify_isolated_target(plan, timeout=0.7)
        self.assertEqual(self.now, 0.7)
        self.target.group.members.append(self.coord)
        self.sleep.reset_mock()
        with self.assertRaises(topology.TopologyError):
            topology.verify_isolated_target(plan, timeout=5)
        self.sleep.assert_not_called()

    def test_unsafe_sample_after_read_failure_is_not_retried_into_acceptance(self):
        plan = topology.validate_announcement_target(self.target)
        safe = topology.inspect_topology(self.target)
        self.target.group.members.append(self.coord)
        unsafe = topology.inspect_topology(self.target)
        with patch.object(topology, 'inspect_topology', side_effect=[
            topology.TopologyUnavailable(), unsafe, safe,
        ]) as inspect:
            with self.assertRaises(topology.TopologyError):
                topology.verify_isolated_target(plan, timeout=5)
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(self.now, 0.5)

    def test_recovery_retries_either_unavailable_room_without_replaying_join(self):
        for failing_uid in ('target', 'coord'):
            with self.subTest(failing_uid=failing_uid):
                plan = self.grouped_plan()
                inspect = topology.inspect_topology
                failed = []
                def transient(speaker):
                    if speaker.uid == failing_uid and not failed:
                        failed.append(True)
                        raise topology.TopologyUnavailable()
                    return inspect(speaker)
                with patch.object(topology, 'inspect_topology', side_effect=transient):
                    topology.restore_target_group(plan, timeout=1)
                self.target.join.assert_called_once_with(self.coord)
                self.target.join.reset_mock()

    def test_recovery_unavailable_is_bounded_without_join(self):
        plan = self.grouped_plan()
        with patch.object(topology, 'inspect_topology',
                          side_effect=topology.TopologyUnavailable()):
            with self.assertRaises(topology.TopologyError):
                topology.restore_target_group(plan, timeout=0.7)
        self.assertEqual(self.now, 0.7)
        self.target.join.assert_not_called()

    def test_post_join_uses_remaining_deadline(self):
        plan = self.grouped_plan()
        inspect = topology.inspect_topology
        failed = []
        def transient(speaker):
            if not failed:
                failed.append(True)
                raise topology.TopologyUnavailable()
            return inspect(speaker)
        self.target.join.side_effect = None  # Publication never converges.
        with patch.object(topology, 'inspect_topology', side_effect=transient):
            with self.assertRaises(topology.TopologyError):
                topology.restore_target_group(plan, timeout=0.7)
        self.assertEqual(self.now, 0.7)
        self.target.join.assert_called_once()


class TestIdempotentTopology(unittest.TestCase):
    def setUp(self):
        self.h = _Household()
        self.target, self.coord = _CachedSpeaker('target', self.h), _CachedSpeaker('coord', self.h)
        self.h.group_members(self.coord, [self.coord, self.target])
        self.clock = patch.object(topology.time, 'monotonic', side_effect=lambda: self.h.now)
        self.sleep = patch.object(topology.time, 'sleep', side_effect=self.h.sleep)
        self.clock.start(); self.sleep.start()
        self.addCleanup(self.clock.stop); self.addCleanup(self.sleep.stop)
        self.plan = topology.inspect_topology(self.target)

    def test_already_original_returns_without_join_or_delay(self):
        samples = self.h.samples
        topology.restore_target_group(self.plan)
        self.target.join.assert_not_called()
        self.assertEqual(self.h.now, 0)
        self.assertGreater(self.h.samples, samples)

    def test_recovery_armed_then_interrupt_before_rpc_is_noop(self):
        with self.assertRaises(KeyboardInterrupt):
            topology.detach_target(self.plan, before_unjoin=lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
        topology.restore_target_group(self.plan)
        self.target.unjoin.assert_not_called()
        self.target.join.assert_not_called()

    def test_wait_cycles_are_half_second_and_deadline_bounded(self):
        with self.assertRaises(topology.TopologyError):
            topology._wait_for_topology(self.target, 'target', frozenset({'target'}), 5)
        self.assertEqual(self.h.now, 5)
        self.assertEqual(self.h.samples, 12)  # plan plus initial sample and ten cycles
        self.assertEqual(topology.TOPOLOGY_POLL_SECONDS, 0.5)
        self.h.now = 0
        with self.assertRaises(topology.TopologyError):
            topology._wait_for_topology(self.target, 'target', frozenset({'target'}), 0.2)
        self.assertEqual(self.h.now, 0.2)

    def test_unrelated_target_group_is_not_rejoined(self):
        other = _CachedSpeaker('other', self.h)
        self.h.group_members(self.coord, [self.coord])
        self.h.group_members(other, [other, self.target])
        with self.assertRaises(topology.TopologyError):
            topology.restore_target_group(self.plan, timeout=0)
        self.target.join.assert_not_called()


_SDK_TOPOLOGY = r'''
import sys
from unittest.mock import patch
import soco
from soco.zonegroupstate import ZoneGroupState
import sonos_topology as topology
assert soco.__version__ == '0.31.2'
primary_xml = """
<ZoneGroupMember UUID="primary" ZoneName="Same Room"
 Location="http://192.0.2.2:1400/xml/device_description.xml">
 <Satellite UUID="left" ZoneName="Same Room" Invisible="1"
  Location="http://192.0.2.3:1400/xml/device_description.xml"/>
 <Satellite UUID="right" ZoneName="Same Room" Invisible="1"
  Location="http://192.0.2.4:1400/xml/device_description.xml"/>
</ZoneGroupMember>"""
other_xml = """
<ZoneGroupMember UUID="other" ZoneName="Same Room"
 Location="http://192.0.2.5:1400/xml/device_description.xml"/>"""
def document(extra=''):
    return ('<ZoneGroupState><ZoneGroups><ZoneGroup Coordinator="primary" '
            'ID="primary:1">' + primary_xml + extra +
            '</ZoneGroup></ZoneGroups></ZoneGroupState>')
state = ZoneGroupState()
current = [document()]
calls = [0]
def poll(speaker):
    calls[0] += 1
    if sys.argv[1] == 'changing' and calls[0] == 2:
        current[0] = document(other_xml)
    state.process_payload(current[0], source='test', source_ip='192.0.2.2')
state.process_payload(current[0], source='test', source_ip='192.0.2.2')
primary = next(iter(state.groups)).coordinator
def primary_state(speaker):
    assert speaker is primary, 'satellite queried for roles'
    return state
with patch.object(soco.SoCo, 'zone_group_state', property(primary_state)), \
     patch.object(state, 'poll', poll):
    if sys.argv[1] == 'changing':
        # No getter can trigger a second poll while capturing roles.
        plan = topology.validate_announcement_target(primary)
        assert calls[0] == 1
        assert plan.room_member_uids == frozenset({'primary', 'left', 'right'})
        try:
            topology.verify_isolated_target(plan)
        except topology.TopologyError:
            assert calls[0] == 2 and state.processed_count == 2
        else:
            raise AssertionError('later unsafe observation accepted')
        print('single observation copied; later drift refused')
    else:
        plan = topology.validate_announcement_target(primary)
        assert calls[0] == 1
        assert plan.room_member_uids == frozenset({'primary', 'left', 'right'})
        assert {(member.uid, member.parent_uid) for member in plan.member_topology
                if member.satellite} == {('left', 'primary'), ('right', 'primary')}
        assert not plan.detach
        topology.verify_isolated_target(plan)
        current[0] = document(other_xml)
        try:
            topology.validate_announcement_target(primary)
        except topology.TopologyError:
            pass
        else:
            raise AssertionError('second logical room accepted')
        print('owned satellites accepted; same-named second room refused')
'''


class TestInstalledTopologyContracts(unittest.TestCase):
    def run_contract(self, mode, expected):
        result = subprocess.run(
            [sys.executable, '-c', _SDK_TOPOLOGY, mode],
            env={'PATH': os.defpath, 'PYTHONPATH': str(Path(__file__).parent),
                 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(expected, result.stdout)

    def test_owned_satellites_and_multiroom_partition_with_real_sdk(self):
        self.run_contract('stable', 'owned satellites accepted; same-named second room refused')

    def test_roles_use_one_payload_and_later_drift_is_refused_with_real_sdk(self):
        self.run_contract('changing', 'single observation copied; later drift refused')

    def test_real_soco_cached_uid_cannot_authorize_replacement_device(self):
        code = r'''
import http.client
from unittest.mock import patch
import soco
import sonos_topology as topology
assert soco.__version__ == '0.31.2'
speaker = soco.SoCo('192.0.2.2')
speaker._uid = 'cached-primary'
plan = topology.TopologyPlan(
    coordinator=speaker, coordinator_uid=speaker.uid, detach=False,
    member_uids=frozenset({speaker.uid}), member_topology=(),
    room_member_uids=frozenset({speaker.uid}), speaker=speaker, speaker_uid=speaker.uid)
with patch.object(http.client, 'HTTPConnection') as connection:
    response = connection.return_value.getresponse.return_value
    response.status = 200
    response.read.return_value = b'<root xmlns="urn:schemas-upnp-org:device-1-0"><device><UDN>uuid:replacement</UDN></device></root>'
    try:
        topology.verify_target_identity(plan)
    except topology.TopologyError:
        assert speaker.uid == 'cached-primary'
        connection.return_value.request.assert_called_once()
    else:
        raise AssertionError('cached UID authorized replacement')
print('real cached UID ignored; replacement refused')
'''
        result = subprocess.run(
            [sys.executable, '-c', code],
            env={'PATH': os.defpath, 'PYTHONPATH': str(Path(__file__).parent),
                 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('real cached UID ignored; replacement refused', result.stdout)


if __name__ == "__main__":
    unittest.main()
