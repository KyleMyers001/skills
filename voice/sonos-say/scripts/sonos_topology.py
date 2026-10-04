"""Strict Sonos group inspection and temporary target isolation."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Optional


TOPOLOGY_POLL_SECONDS = 0.1
TOPOLOGY_VERIFY_TIMEOUT = 5.0


class TopologyError(RuntimeError):
    """The target's group topology is unsafe or could not be restored."""


@dataclass(frozen=True)
class TopologyPlan:
    """Validated topology needed to isolate and restore one speaker."""

    coordinator: Any
    coordinator_uid: str
    detach: bool
    member_uids: frozenset[str]
    members: tuple[Any, ...]
    speaker: Any
    speaker_uid: str


def _uid(speaker: Any) -> str:
    try:
        uid = speaker.uid
    except Exception as exc:
        raise TopologyError("speaker identity is unavailable") from exc
    if not isinstance(uid, str) or not uid.strip():
        raise TopologyError("speaker identity is malformed")
    return uid


def inspect_topology(speaker: Any) -> TopologyPlan:
    """Return a fail-closed plan for a standalone, coordinator, or member."""
    try:
        state = speaker.zone_group_state
        # SoCo 0.31.2 skips polling under subscriptions, even after invalidation.
        # Require a network observation rather than silently accepting that cache.
        if state.has_subscriptions is not False:
            raise TopologyError("fresh polling is unavailable under subscriptions")
        state.clear_cache()
        group = speaker.group
        if state.has_subscriptions is not False:
            raise TopologyError("fresh polling is unavailable under subscriptions")
        coordinator = group.coordinator
        members = list(group.members)
    except Exception as exc:
        raise TopologyError("group topology is unavailable") from exc
    if not members:
        raise TopologyError("group topology has no members")

    speaker_uid = _uid(speaker)
    coordinator_uid = _uid(coordinator)
    member_uids = [_uid(member) for member in members]
    if len(member_uids) != len(set(member_uids)):
        raise TopologyError("group topology has duplicate members")
    if member_uids.count(speaker_uid) != 1:
        raise TopologyError("target is not uniquely present in its group")
    if member_uids.count(coordinator_uid) != 1:
        raise TopologyError("coordinator is not uniquely present in its group")

    target_is_coordinator = speaker_uid == coordinator_uid
    if len(members) == 1 and not target_is_coordinator:
        raise TopologyError("standalone target is not its coordinator")
    return TopologyPlan(
        coordinator=coordinator,
        coordinator_uid=coordinator_uid,
        detach=len(members) > 1 and not target_is_coordinator,
        member_uids=frozenset(member_uids),
        members=tuple(members),
        speaker=speaker,
        speaker_uid=speaker_uid,
    )


def validate_announcement_target(speaker: Any) -> TopologyPlan:
    """Refuse group-wide playback and unsupported bonded/hidden devices.

    Keep these restrictions separate from inspection: restoration must still
    observe coordinators with multiple members without trying to play on them.
    """
    plan = inspect_topology(speaker)
    if len(plan.member_uids) > 1 and not plan.detach:
        raise TopologyError("grouped coordinators cannot receive room announcements")
    try:
        for member in plan.members:
            if (member.is_visible is not True
                    or member.is_satellite is not False
                    or member.has_satellites is not False):
                raise TopologyError("bonded or unknown topology is unsupported")
    except TopologyError:
        raise
    except Exception as exc:
        raise TopologyError("device topology is unavailable") from exc
    return plan


def verify_isolated_target(plan: TopologyPlan) -> None:
    """Recheck the selected identity and isolation immediately before playback."""
    observed = validate_announcement_target(plan.speaker)
    if (observed.speaker_uid != plan.speaker_uid
            or observed.coordinator_uid != plan.speaker_uid
            or observed.member_uids != frozenset({plan.speaker_uid})):
        raise TopologyError("target is not isolated")


def _wait_for_topology(
    speaker: Any,
    expected_coordinator_uid: str,
    expected_member_uids: frozenset[str],
    timeout: float,
) -> TopologyPlan:
    """Wait boundedly for an exact coordinator and membership view."""
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while True:
        try:
            observed = inspect_topology(speaker)
            if (
                observed.coordinator_uid == expected_coordinator_uid
                and observed.member_uids == expected_member_uids
            ):
                return observed
        except Exception as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            raise TopologyError(
                "exact group topology did not converge"
            ) from last_error
        time.sleep(TOPOLOGY_POLL_SECONDS)


def detach_target(
    plan: TopologyPlan,
    timeout: float = TOPOLOGY_VERIFY_TIMEOUT,
    before_unjoin: Optional[Callable[[], None]] = None,
) -> None:
    """Detach only a validated grouped non-coordinator and verify isolation."""
    if not plan.detach:
        return
    observed = validate_announcement_target(plan.speaker)
    if (observed.speaker_uid != plan.speaker_uid
            or observed.coordinator_uid != plan.coordinator_uid
            or observed.member_uids != plan.member_uids):
        raise TopologyError("group changed before isolation")
    # Arm recovery at the RPC boundary, not before validation or after return:
    # the speaker can apply the mutation and then time out.
    if before_unjoin is not None:
        before_unjoin()
    try:
        plan.speaker.unjoin()
    except Exception as exc:
        raise TopologyError("target could not be isolated") from exc
    try:
        _wait_for_topology(
            plan.speaker,
            plan.speaker_uid,
            frozenset({plan.speaker_uid}),
            timeout,
        )
    except Exception as exc:
        raise TopologyError(
            "target isolation could not be verified"
        ) from exc


def restore_target_group(
    plan: TopologyPlan,
    timeout: float = TOPOLOGY_VERIFY_TIMEOUT,
) -> None:
    """Rejoin the target to its exact prior coordinator and verify it."""
    if not plan.detach:
        return
    expected_without_target = plan.member_uids - {plan.speaker_uid}
    try:
        _wait_for_topology(
            plan.coordinator,
            plan.coordinator_uid,
            expected_without_target,
            timeout,
        )
        plan.speaker.join(plan.coordinator)
    except Exception as exc:
        raise TopologyError("target could not rejoin its prior group") from exc
    try:
        _wait_for_topology(
            plan.speaker,
            plan.coordinator_uid,
            plan.member_uids,
            timeout,
        )
    except Exception as exc:
        raise TopologyError(
            "exact prior group membership was not restored"
        ) from exc
