"""Behavior tests for strict temporary Sonos target isolation."""

from __future__ import annotations

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
    speaker.uid = uid
    speaker.is_satellite = False
    speaker.has_satellites = False
    speaker.is_visible = True
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
        for name, value in (("is_satellite", True), ("has_satellites", True),
                            ("is_visible", False), ("has_satellites", None)):
            with self.subTest(name=name, value=value):
                target = _speaker("target")
                _set_group(target, target, [target])
                setattr(target, name, value)
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
        self.assertEqual(plan.satellite_parents,
                         frozenset((sat.uid, primary.uid) for sat in satellites))
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
                 'unknown-visibility', 'missing-satellites', 'duplicate',
                 'unknown-primary', 'contradictory-primary')
        for case in cases:
            with self.subTest(case=case):
                primary, satellites = make_bonded_room()
                if case == 'missing-parent':
                    del satellites[0]._satellite_parent
                elif case == 'foreign-parent':
                    satellites[0]._satellite_parent = FakeSpeaker('foreign')
                elif case == 'unknown-satellite':
                    satellites[0].is_satellite = None
                elif case == 'unknown-visibility':
                    satellites[0].is_visible = None
                elif case == 'missing-satellites':
                    primary.group.members = [primary]
                elif case == 'duplicate':
                    satellites[0].uid = satellites[1].uid
                elif case == 'unknown-primary':
                    primary.has_satellites = None
                elif case == 'contradictory-primary':
                    primary.has_satellites = False
                with self.assertRaises(topology.TopologyError):
                    topology.validate_announcement_target(primary)
                primary.unjoin.assert_not_called()
                primary.play_uri.assert_not_called()

    def test_direct_satellite_and_hidden_primary_are_refused(self):
        primary, satellites = make_bonded_room()
        with self.assertRaises(topology.TopologyError):
            topology.validate_announcement_target(satellites[0])
        primary.is_visible = False
        with self.assertRaises(topology.TopologyError):
            topology.validate_announcement_target(primary)

    def test_satellite_primary_only_flags_do_not_determine_ownership(self):
        primary, satellites = make_bonded_room()
        satellites[0].has_satellites = None
        satellites[1].has_satellites = True
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
                saved_parents = frozenset((sat.uid, primary.uid) for sat in satellites)
                if change == 'parent':
                    satellites[0]._satellite_parent = FakeSpeaker('foreign')
                elif change == 'remove':
                    primary.group.members.remove(satellites[0])
                elif change == 'add':
                    extra = FakeSpeaker('new-satellite')
                    extra.is_visible, extra.is_satellite = False, True
                    extra._satellite_parent = primary
                    primary.group.members.append(extra)
                else:
                    primary.group.coordinator = satellites[0]
                self.assertEqual(plan.satellite_parents, saved_parents)
                self.assertEqual(len(plan.room_member_uids), 3)
                with self.assertRaises(topology.TopologyError):
                    topology.verify_isolated_target(plan)

    def test_raw_inspection_keeps_unknown_eligibility_flags(self):
        primary, satellites = make_bonded_room()
        primary.has_satellites = None
        plan = topology.inspect_topology(primary)
        self.assertEqual(len(plan.member_uids), 3)
        self.assertIsNone(next(member for member in plan.member_topology
                               if member.uid == primary.uid).has_satellites)
        with self.assertRaises(topology.TopologyError):
            topology.validate_announcement_target(primary)

    def test_role_getters_cannot_mix_processed_topology_observations(self):
        primary = _speaker('target')
        _set_group(primary, primary, [primary])
        def newer_sample():
            primary.zone_group_state.processed_count += 1
            return True
        with patch.object(type(primary), 'is_visible', create=True,
                          new_callable=PropertyMock, side_effect=newer_sample):
            with self.assertRaises(topology.TopologyError):
                topology.validate_announcement_target(primary)

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

    def group_members(self, coordinator, members):
        group = SimpleNamespace(coordinator=coordinator, members=members)
        for member in members:
            self.live[member.uid] = group

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


class _CachedSpeaker:
    is_satellite = False
    has_satellites = False
    is_visible = True

    def __init__(self, uid, household):
        self.uid = uid
        self.zone_group_state = household
        self.unjoin = MagicMock()
        self.join = MagicMock()

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
with patch.object(soco.SoCo, 'zone_group_state', property(lambda self: state)), \
     patch.object(state, 'poll', poll):
    if sys.argv[1] == 'changing':
        try:
            topology.validate_announcement_target(primary)
        except topology.TopologyError:
            assert state.processed_count == 2
        else:
            raise AssertionError('mixed processed observations accepted')
        print('mixed observations refused')
    else:
        plan = topology.validate_announcement_target(primary)
        assert plan.room_member_uids == frozenset({'primary', 'left', 'right'})
        assert plan.satellite_parents == frozenset({('left', 'primary'), ('right', 'primary')})
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

    def test_new_payload_during_role_reads_fails_closed_with_real_sdk(self):
        self.run_contract('changing', 'mixed observations refused')


if __name__ == "__main__":
    unittest.main()
