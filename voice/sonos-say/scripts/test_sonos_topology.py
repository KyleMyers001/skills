"""Behavior tests for strict temporary Sonos target isolation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import sonos_topology as topology


def _speaker(uid: str) -> MagicMock:
    speaker = MagicMock()
    speaker.uid = uid
    speaker.is_satellite = False
    speaker.has_satellites = False
    speaker.is_visible = True
    speaker.zone_group_state.has_subscriptions = False
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


class _Household:
    """SoCo's shared five-second polling cache, independent of live state."""

    def __init__(self):
        self.now = 0.0
        self.live = {}
        self.cached = {}
        self.cache_until = -1.0
        self.has_subscriptions = False
        self.samples = 0
        self.on_sleep = None

    def clear_cache(self):
        self.cache_until = -1.0

    def group(self, uid):
        if not self.has_subscriptions and self.now >= self.cache_until:
            self.cached = dict(self.live)
            self.cache_until = self.now + 5
            self.samples += 1
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


if __name__ == "__main__":
    unittest.main()
