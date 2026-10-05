"""Strict Sonos group inspection and temporary target isolation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import http.client
import ipaddress
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional


TOPOLOGY_POLL_SECONDS = 0.5
TOPOLOGY_VERIFY_TIMEOUT = 5.0
DEVICE_DESCRIPTION_LIMIT = 64 * 1024


class TopologyError(RuntimeError):
    """The target's group topology is unsafe or could not be restored."""


class TopologyUnavailable(TopologyError):
    """A fresh observation failed; a later read may succeed safely."""


@dataclass(frozen=True)
class _MemberTopology:
    """Device roles copied from one processed SoCo topology observation."""

    uid: str
    visible: bool
    satellite: Any
    has_satellites: Any
    parent_uid: Optional[str]


@dataclass(frozen=True)
class TopologyPlan:
    """Physical grouping and the validated logical room's playback boundary."""

    coordinator: Any
    coordinator_uid: str
    detach: bool
    member_uids: frozenset[str]
    member_topology: tuple[_MemberTopology, ...]
    room_member_uids: frozenset[str]
    speaker: Any
    speaker_uid: str


def _uid(speaker: Any) -> str:
    try:
        uid = speaker._uid
    except Exception as exc:
        raise TopologyError("speaker identity is unavailable") from exc
    if not isinstance(uid, str) or not uid.strip():
        raise TopologyError("speaker identity is malformed")
    return uid


def _satellite_parent_uid(member: Any) -> Optional[str]:
    """SoCo 0.31.2 has no public accessor for parsed satellite ownership."""
    parent = member._satellite_parent
    return None if parent is None else _uid(parent)


def inspect_topology(speaker: Any) -> TopologyPlan:
    """Observe physical grouping without imposing announcement eligibility."""
    try:
        state = speaker.zone_group_state
        # SoCo 0.31.2 skips polling under subscriptions, even after invalidation.
        if state.has_subscriptions is not False:
            raise TopologyError("fresh polling is unavailable under subscriptions")
        state.clear_cache()
        group = speaker.group
        coordinator = group.coordinator
        members = tuple(group.members)
        sample = state.processed_count
        if not isinstance(sample, int) or isinstance(sample, bool):
            raise TopologyError("topology observation identity is unavailable")
        speaker_uid = _uid(speaker)
        coordinator_uid = _uid(coordinator)
        member_topology: list[_MemberTopology] = []
        # SoCo 0.31.2 public role getters poll each member's topology state.
        # Copy only parser-populated fields/visibility from the primary sample;
        # satellites need not answer independent requests to prove ownership.
        visible_zones = state.visible_zones.copy()
        for member in members:
            satellite = member._is_satellite
            member_topology.append(_MemberTopology(
                uid=_uid(member),
                visible=member in visible_zones,
                satellite=satellite,
                has_satellites=member._has_satellites,
                parent_uid=(_satellite_parent_uid(member)
                            if satellite is True else None),
            ))
        # Retain the guard against asynchronous changes to the shared state.
        if (state.has_subscriptions is not False
                or state.processed_count != sample):
            raise TopologyError("topology changed during observation")
    except TopologyError:
        raise
    except Exception as exc:
        raise TopologyUnavailable("group topology is unavailable") from exc
    if not members:
        raise TopologyError("group topology has no members")

    member_uids = [member.uid for member in member_topology]
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
        member_topology=tuple(member_topology),
        room_member_uids=frozenset({speaker_uid}),
        speaker=speaker,
        speaker_uid=speaker_uid,
    )


def validate_announcement_target(speaker: Any) -> TopologyPlan:
    """Allow one logical room, never multi-room coordinator playback."""
    plan = inspect_topology(speaker)
    target = next(member for member in plan.member_topology
                  if member.uid == plan.speaker_uid)
    if target.visible is not True or target.satellite is not False:
        raise TopologyError("hidden or satellite targets are unsupported")

    if target.has_satellites is True:
        if plan.detach or len(plan.member_uids) == 1:
            raise TopologyError("bonded room is grouped or malformed")
        for member in plan.member_topology:
            if member.uid == plan.speaker_uid:
                continue
            if (member.satellite is not True
                    or member.parent_uid != plan.speaker_uid):
                raise TopologyError("bonded room is grouped or malformed")
        return replace(plan, room_member_uids=plan.member_uids)

    for member in plan.member_topology:
        if (member.visible is not True or member.satellite is not False
                or member.has_satellites is not False):
            raise TopologyError("bonded or unknown topology is unsupported")
    if len(plan.member_uids) > 1 and not plan.detach:
        raise TopologyError("grouped coordinators cannot receive room announcements")
    return plan


def _read_device_uid(speaker: Any) -> str:
    """Read UDN directly: even get_speaker_info(refresh=True) uses cached uid."""
    connection = None
    try:
        host = str(ipaddress.IPv4Address(speaker.ip_address))
        connection = http.client.HTTPConnection(
            host, 1400, timeout=TOPOLOGY_VERIFY_TIMEOUT,
        )
        connection.request("GET", "/xml/device_description.xml")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("device description request failed")
        body = response.read(DEVICE_DESCRIPTION_LIMIT + 1)
        if len(body) > DEVICE_DESCRIPTION_LIMIT:
            raise ValueError("device description is too large")
        udn = ET.fromstring(body).findtext(
            "{urn:schemas-upnp-org:device-1-0}device/"
            "{urn:schemas-upnp-org:device-1-0}UDN"
        )
        if not isinstance(udn, str) or not udn.startswith("uuid:"):
            raise ValueError("device UDN is unavailable")
        uid = udn[5:]
        if not uid or uid != uid.strip():
            raise ValueError("device UDN is malformed")
        return uid
    except Exception as exc:
        raise TopologyError("live target identity is unavailable") from exc
    finally:
        if connection is not None:
            connection.close()


def verify_target_identity(plan: TopologyPlan) -> None:
    """Rendering cleanup must never trust a cached UID at a reassigned IP."""
    if _read_device_uid(plan.speaker) != plan.speaker_uid:
        raise TopologyError("target identity changed")


def verify_isolated_target(
    plan: TopologyPlan, timeout: float = 0,
) -> None:
    """Retry unavailable reads only; observed unsafe rooms fail immediately."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            observed = validate_announcement_target(plan.speaker)
        except TopologyUnavailable:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(TOPOLOGY_POLL_SECONDS, remaining))
            continue
        # Eligibility already proves every satellite belongs to this primary.
        # Exact membership thus also proves the captured ownership links.
        if (observed.speaker_uid != plan.speaker_uid
                or observed.coordinator_uid != plan.speaker_uid
                or observed.member_uids != plan.room_member_uids):
            raise TopologyError("target room is not isolated or its bonds changed")
        return


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
        time.sleep(min(TOPOLOGY_POLL_SECONDS, max(0, deadline - time.monotonic())))


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
    deadline = time.monotonic() + timeout
    while True:
        try:
            target = inspect_topology(plan.speaker)
            coordinator = inspect_topology(plan.coordinator)
        except TopologyUnavailable:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(TOPOLOGY_POLL_SECONDS, remaining))
            continue
        if (target.speaker_uid != plan.speaker_uid
                or coordinator.speaker_uid != plan.coordinator_uid):
            raise TopologyError("recovery device identity changed")
        original = (
            target.coordinator_uid == coordinator.coordinator_uid == plan.coordinator_uid
            and target.member_uids == coordinator.member_uids == plan.member_uids
        )
        if original:
            return  # RPC may have failed before mutation, or rejoin already completed.
        detached = (
            target.coordinator_uid == plan.speaker_uid
            and target.member_uids == frozenset({plan.speaker_uid})
            and coordinator.coordinator_uid == plan.coordinator_uid
            and coordinator.member_uids == expected_without_target
        )
        if detached:
            break
        if (target.coordinator_uid not in (plan.speaker_uid, plan.coordinator_uid)
                or coordinator.coordinator_uid != plan.coordinator_uid
                or not target.member_uids <= plan.member_uids
                or not coordinator.member_uids <= plan.member_uids):
            raise TopologyError("unrelated topology cannot be restored safely")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TopologyError("recovery topology did not converge")
        time.sleep(min(TOPOLOGY_POLL_SECONDS, remaining))
    try:
        plan.speaker.join(plan.coordinator)
    except Exception as exc:
        raise TopologyError("target could not rejoin its prior group") from exc
    try:
        _wait_for_topology(
            plan.speaker,
            plan.coordinator_uid,
            plan.member_uids,
            max(0, deadline - time.monotonic()),
        )
    except Exception as exc:
        raise TopologyError(
            "exact prior group membership was not restored"
        ) from exc
