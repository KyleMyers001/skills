"""Strict Sonos group inspection and temporary target isolation."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import http.client
import ipaddress
import socket
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional


TOPOLOGY_POLL_SECONDS = 0.5
TOPOLOGY_VERIFY_TIMEOUT = 5.0
# requests rejects a zero timeout; a read started at its deadline still gets one.
TOPOLOGY_MIN_READ_SECONDS = 0.5
DEVICE_DESCRIPTION_LIMIT = 64 * 1024


def _request_limit(seconds: float) -> float:
    return min(TOPOLOGY_VERIFY_TIMEOUT, max(TOPOLOGY_MIN_READ_SECONDS, seconds))


@contextmanager
def bounded_soco_request(seconds: float):
    """Cap one SoCo call and do not open a topology subscription."""
    import soco.config as config
    previous_timeout = config.REQUEST_TIMEOUT
    previous_fallback = config.ZGT_EVENT_FALLBACK
    limit = _request_limit(seconds)
    if (isinstance(previous_timeout, (int, float))
            and not isinstance(previous_timeout, bool)
            and previous_timeout < limit):
        limit = previous_timeout
    config.REQUEST_TIMEOUT = limit
    config.ZGT_EVENT_FALLBACK = False
    try:
        yield
    finally:
        config.REQUEST_TIMEOUT = previous_timeout
        config.ZGT_EVENT_FALLBACK = previous_fallback


class _Deadline:
    """One check's retry budget. Sleeps are never longer than time left."""

    def __init__(self, timeout: float):
        self.deadline = time.monotonic() + timeout

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def pause(self) -> bool:
        remaining = self.remaining()
        if remaining <= 0:
            return False
        time.sleep(min(TOPOLOGY_POLL_SECONDS, remaining))
        return True


def _unsupported_observation_error(exc: BaseException) -> bool:
    """Large households answer GetZoneGroupState with 501; retrying cannot help."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status == 501 or str(getattr(exc, "error_code", "")) == "501"


def _temporary_observation_error(exc: BaseException) -> bool:
    """Network stalls may be retried; malformed topology must not."""
    if isinstance(exc, (TimeoutError, OSError, RuntimeError)):
        return True
    try:
        from soco.exceptions import SoCoException
    except Exception:
        return False
    return isinstance(exc, SoCoException)


def _poll(
    read: Callable[[float], Any],
    timeout: float,
    *,
    accept: Optional[Callable[[Any], bool]] = None,
    wait_if_rejected: bool = False,
    retry_structural: bool = False,
    rejected: str = "topology did not converge",
):
    """Retry unavailable reads; retry structural read errors only on request.

    Mid-transition samples after a group change may be structurally
    incomplete. Errors raised by ``accept`` always propagate.
    """
    retryable = TopologyError if retry_structural else TopologyUnavailable
    deadline = _Deadline(timeout)
    while True:
        last_error: Optional[BaseException] = None
        if timeout <= 0:
            call_timeout = TOPOLOGY_VERIFY_TIMEOUT
        else:
            call_timeout = _request_limit(deadline.remaining())
        try:
            observed = read(call_timeout)
        except retryable as exc:
            last_error = exc
        else:
            if accept is None or accept(observed):
                return observed
            if not wait_if_rejected:
                raise TopologyError(rejected)
        if not deadline.pause():
            if (isinstance(last_error, TopologyUnavailable)
                    and not wait_if_rejected):
                raise last_error
            raise TopologyError(rejected) from last_error


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


def _fetch_zone_group_state(speaker: Any, timeout: float) -> Any:
    """Process one fresh GetZoneGroupState payload under an explicit timeout.

    Explicit empty argument lists matter: SoCo 0.31.2 composes implicit
    arguments by downloading the service description with a fixed 10-second
    timeout. Polling directly also never opens SoCo's ZGT event fallback.
    """
    if speaker._household_id is None:
        # SoCo keys the shared state by household; its getter is unbounded.
        speaker._household_id = speaker.deviceProperties.GetHouseholdID(
            [], timeout=timeout,
        )["CurrentHouseholdID"]
    state = speaker.zone_group_state
    # SoCo 0.31.2 lets subscription events replace polled state at any time.
    if state.has_subscriptions is not False:
        raise TopologyError("fresh polling is unavailable under subscriptions")
    payload = speaker.zoneGroupTopology.GetZoneGroupState(
        [], timeout=timeout,
    )["ZoneGroupState"]
    state.process_payload(
        payload=payload, source="poll", source_ip=speaker.ip_address,
    )
    # SoCo's group lookup polls on cache expiry; make it read this payload.
    state._cache_until = time.monotonic() + TOPOLOGY_VERIFY_TIMEOUT
    return state


def inspect_topology(
    speaker: Any, *, timeout: float = TOPOLOGY_VERIFY_TIMEOUT,
) -> TopologyPlan:
    """Observe physical grouping without imposing announcement eligibility."""
    try:
        state = _fetch_zone_group_state(speaker, _request_limit(timeout))
        group = speaker.group
        if group is None:
            raise TopologyError("group topology is missing")
        try:
            coordinator = group.coordinator
            members = tuple(group.members)
        except AttributeError as exc:
            raise TopologyError("group topology is malformed") from exc
        sample = state.processed_count
        if not isinstance(sample, int) or isinstance(sample, bool):
            raise TopologyError(
                "topology observation identity is unavailable"
            )
        speaker_uid = _uid(speaker)
        coordinator_uid = _uid(coordinator)
        member_topology: list[_MemberTopology] = []
        # SoCo 0.31.2 public role getters poll each member's topology state.
        # Copy only parser-populated fields/visibility from the primary sample;
        # satellites need not answer independent requests to prove ownership.
        visible_zones = state.visible_zones.copy()
        for member in members:
            try:
                satellite = member._is_satellite
                has_satellites = member._has_satellites
                parent_uid = (
                    _satellite_parent_uid(member)
                    if satellite is True else None
                )
            except AttributeError as exc:
                raise TopologyError("speaker role is unavailable") from exc
            member_topology.append(_MemberTopology(
                uid=_uid(member),
                visible=member in visible_zones,
                satellite=satellite,
                has_satellites=has_satellites,
                parent_uid=parent_uid,
            ))
        # Retain the guard against asynchronous changes to the shared state.
        if (state.has_subscriptions is not False
                or state.processed_count != sample):
            raise TopologyError("topology changed during observation")
    except TopologyError:
        raise
    except Exception as exc:
        if _unsupported_observation_error(exc):
            raise TopologyError(
                "group topology polling is unsupported by this household"
            ) from exc
        if _temporary_observation_error(exc):
            raise TopologyUnavailable(
                "group topology is unavailable"
            ) from exc
        raise TopologyError("group topology is malformed") from exc
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


def validate_announcement_target(
    speaker: Any, *, timeout: float = TOPOLOGY_VERIFY_TIMEOUT,
) -> TopologyPlan:
    """Allow one logical room, never multi-room coordinator playback."""
    plan = inspect_topology(speaker, timeout=timeout)
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


def _fetch_device_description(connection: http.client.HTTPConnection) -> bytes:
    connection.request("GET", "/xml/device_description.xml")
    response = connection.getresponse()
    if response.status != 200:
        raise ValueError("device description request failed")
    return response.read(DEVICE_DESCRIPTION_LIMIT + 1)


def _read_device_uid(speaker: Any) -> str:
    """Read UDN directly: even get_speaker_info(refresh=True) uses cached uid.

    Socket timeouts restart on every received byte, so a trickling responder
    could outlast them. A worker thread gives the whole exchange one deadline.
    """
    connection = None
    try:
        host = str(ipaddress.IPv4Address(speaker.ip_address))
        connection = http.client.HTTPConnection(
            host, 1400, timeout=TOPOLOGY_VERIFY_TIMEOUT,
        )
        outcome: dict[str, Any] = {}

        def fetch() -> None:
            try:
                outcome["body"] = _fetch_device_description(connection)
            except Exception as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=fetch, daemon=True)
        worker.start()
        worker.join(TOPOLOGY_VERIFY_TIMEOUT)
        if worker.is_alive():
            sock = connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)  # wake the blocked read
                except OSError:
                    pass
            raise TimeoutError("device description request timed out")
        if "error" in outcome:
            raise outcome["error"]
        body = outcome["body"]
        if len(body) > DEVICE_DESCRIPTION_LIMIT:
            raise ValueError("device description is too large")
        folded = body.upper()
        if b"<!DOCTYPE" in folded or b"<!ENTITY" in folded:
            raise ValueError("device description contains a forbidden declaration")
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
    def isolated(observed: TopologyPlan) -> bool:
        # Eligibility already proves every satellite belongs to this primary.
        # Exact membership thus also proves the captured ownership links.
        return (
            observed.speaker_uid == plan.speaker_uid
            and observed.coordinator_uid == plan.speaker_uid
            and observed.member_uids == plan.room_member_uids
        )

    _poll(
        lambda call_timeout: validate_announcement_target(
            plan.speaker, timeout=call_timeout,
        ),
        timeout,
        accept=isolated,
        rejected="target room is not isolated or its bonds changed",
    )


def _wait_for_topology(
    speaker: Any,
    expected_coordinator_uid: str,
    expected_member_uids: frozenset[str],
    timeout: float,
) -> TopologyPlan:
    """Wait boundedly for an exact coordinator and membership view."""
    return _poll(
        lambda call_timeout: inspect_topology(speaker, timeout=call_timeout),
        timeout,
        accept=lambda observed: (
            observed.coordinator_uid == expected_coordinator_uid
            and observed.member_uids == expected_member_uids
        ),
        wait_if_rejected=True,
        retry_structural=True,
        rejected="exact group topology did not converge",
    )


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
    """Rejoin the target to its exact prior coordinator and verify it.

    ``timeout`` bounds each phase: the pre-join wait, the join RPC, and the
    post-join wait.
    """
    if not plan.detach:
        return
    expected_without_target = plan.member_uids - {plan.speaker_uid}

    def read(call_timeout: float):
        return (
            inspect_topology(plan.speaker, timeout=call_timeout),
            inspect_topology(plan.coordinator, timeout=call_timeout),
        )

    def classify(observed) -> Optional[str]:
        target, coordinator = observed
        if (target.speaker_uid != plan.speaker_uid
                or coordinator.speaker_uid != plan.coordinator_uid):
            raise TopologyError("recovery device identity changed")
        if (target.coordinator_uid == coordinator.coordinator_uid
                == plan.coordinator_uid
                and target.member_uids == coordinator.member_uids
                == plan.member_uids):
            return "original"
        if (target.coordinator_uid == plan.speaker_uid
                and target.member_uids == frozenset({plan.speaker_uid})
                and coordinator.coordinator_uid == plan.coordinator_uid
                and coordinator.member_uids == expected_without_target):
            return "detached"
        if (target.coordinator_uid not in (plan.speaker_uid, plan.coordinator_uid)
                or coordinator.coordinator_uid != plan.coordinator_uid
                or not target.member_uids <= plan.member_uids
                or not coordinator.member_uids <= plan.member_uids):
            raise TopologyError("unrelated topology cannot be restored safely")
        return None

    observed = _poll(
        read, timeout, accept=lambda sample: classify(sample) is not None,
        wait_if_rejected=True, retry_structural=True,
        rejected="recovery topology did not converge",
    )
    if classify(observed) == "original":
        return  # RPC may have failed before mutation, or rejoin already completed.
    with bounded_soco_request(timeout if timeout > 0 else TOPOLOGY_VERIFY_TIMEOUT):
        try:
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
