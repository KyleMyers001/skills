"""Behavior tests for strict temporary Sonos target isolation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import sonos_topology as topology


def _speaker(uid: str) -> MagicMock:
    speaker = MagicMock()
    speaker.uid = uid
    speaker.is_satellite = False
    speaker.has_satellites = False
    speaker.is_visible = True
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


if __name__ == "__main__":
    unittest.main()
