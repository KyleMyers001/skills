#!/usr/bin/env python3
"""
sonos-say — TTS announcement to a Sonos speaker via ElevenLabs + SoCo.

Pipeline:
  1. Validate configuration; install scoped signal handling
  2. Discover and freshly validate the target Sonos topology
  3. Fetch the Keychain API key; synthesise text → temporary MP3
  4. Isolate a grouped member; snapshot transport state + volume
  5. Serve the MP3 on a target-IP-restricted capability URL
  6. Freshly recheck isolation, play, and require observed completion
  7. Restore state + grouping, clean resources, then report the outcome

Usage:
    sonos-say "Hello, world!"
    echo "Hello" | sonos-say -
    sonos-say --room "Test Room" --volume 44 "Text here"
"""

from __future__ import annotations

import argparse
import contextlib
from collections import deque
import http.server
import http.client
import ipaddress
import logging
import math
import os
import secrets
import signal
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

_POLICY_DIR = (
    Path(__file__).resolve().parents[2]
    / "elevenlabs-voice-policy"
    / "scripts"
)
if str(_POLICY_DIR) not in sys.path:
    sys.path.insert(0, str(_POLICY_DIR))

import elevenlabs_voice as voice_policy  # noqa: E402
from elevenlabs_voice import (  # noqa: E402
    DEFAULT_MODEL_ID,
    STABILITY_PRESETS,
    StabilityPreset,
    resolve_stability,
    validate_model_id,
    validate_voice_id,
)
from sonos_topology import (  # noqa: E402
    TopologyError,
    TOPOLOGY_VERIFY_TIMEOUT,
    detach_target,
    restore_target_group,
    validate_announcement_target,
    verify_isolated_target,
    verify_target_identity,
)

# ---------------------------------------------------------------------------
# Optional imports — give actionable errors if missing
# ---------------------------------------------------------------------------
try:
    import soco
    from soco.snapshot import Snapshot
except ImportError:
    print(
        "ERROR: SoCo is not installed. Run: pip install soco",
        file=sys.stderr,
    )
    sys.exit(10)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------
DEFAULT_VOLUME = 35
DEFAULT_MODEL = DEFAULT_MODEL_ID
DEFAULT_TIMEOUT = None        # automatic MP3-size-derived playback budget
DEFAULT_TTS_TIMEOUT = 180      # independent request/header/body timeout
MAX_TEXT_CHARS = 5_000         # implementation limit; keep speech short
# Maximum seconds to wait for transport to enter PLAYING before
# accepting that playback never started (avoids restoring too early on a slow
# Sonos transition).
PLAYBACK_START_TIMEOUT = 8.0
ELEVENLABS_TTS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
OUTPUT_FORMAT = "mp3_44100_128"

log = logging.getLogger("sonos_say")

ERROR_CODES = frozenset({
    "ELEVENLABS_KEY_FAILED",
    "ELEVENLABS_SYNTHESIS_FAILED",
    "SONOS_DISCOVERY_FAILED",
    "SONOS_PLAYBACK_FAILED",
    "SONOS_RESTORE_FAILED",
    "SONOS_TOPOLOGY_FAILED",
    "SONOS_SOURCE_UNSUPPORTED",
})


class SonosSayError(RuntimeError):
    """A phase-specific failure safe to expose across process boundaries."""

    def __init__(self, code: str):
        if code not in ERROR_CODES:
            raise ValueError("unsupported sonos-say error code")
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

def _validate_elevenlabs_id(value: str, label: str) -> str:
    """
    Validate an ElevenLabs voice-id or model identifier.

    Models also accept dots through the shared model validator. Voice IDs
    accept letters, digits, underscores, and hyphens; 1-64 characters.
    Raises ValueError with a human-readable message on failure.
    Returns *value* unchanged on success.
    """
    if label == "model":
        return validate_model_id(value)
    return validate_voice_id(value)


# ---------------------------------------------------------------------------
# Keychain
# ---------------------------------------------------------------------------

def get_api_key(service: str, account: str) -> str:
    """Retrieve ElevenLabs API key from macOS Keychain. Never prints the value."""
    try:
        return voice_policy.keychain_password(service, account)
    except voice_policy.KeychainLookupError:
        raise RuntimeError("Could not retrieve API key from Keychain") from None


# ---------------------------------------------------------------------------
# ElevenLabs TTS
# ---------------------------------------------------------------------------

def synthesise_mp3(
    text: str,
    voice_id: str,
    model: str,
    api_key: str,
    dest_path: Path,
    output_format: str = OUTPUT_FORMAT,
    stability: Optional[StabilityPreset] = None,
    timeout: float = DEFAULT_TTS_TIMEOUT,
) -> None:
    """
    Call ElevenLabs REST API and write the MP3 to *dest_path*.

    *voice_id* is percent-encoded before URL insertion.
    *model* is validated but only used in the JSON body (no URL insertion).
    The API key is never logged.
    """
    _validate_timeout(timeout)
    validate_voice_id(voice_id)
    endpoint = f"{ELEVENLABS_TTS_BASE}/{urllib.parse.quote(voice_id, safe='')}"
    try:
        with voice_policy.open_tts(endpoint, api_key, text, model,
                                   output_format, timeout, stability) as resp:
            data = resp.read()
            if not data:
                raise voice_policy.TTSRequestError()
    except (voice_policy.TTSRequestError, OSError, http.client.HTTPException):
        raise RuntimeError("ElevenLabs request failed") from None

    dest_path.write_bytes(data)
    log.debug("MP3 written (%d bytes) to %s", len(data), dest_path)


# ---------------------------------------------------------------------------
# Multicast / Sonos discovery
# ---------------------------------------------------------------------------

def discover_speaker(room: str, timeout: float = 10.0) -> soco.SoCo:
    """
    Discover Sonos speakers via SoCo (multicast) and return the one whose
    room name matches *room* (case-insensitive).
    """
    log.debug("Discovering Sonos speakers (timeout=%.1fs)…", timeout)
    speakers = list(soco.discover(timeout=timeout) or [])
    if not speakers:
        raise RuntimeError(
            "No Sonos speakers found on the network. "
            "Ensure you are on the same LAN as your Sonos system."
        )
    room_lower = room.lower()
    matches = []
    available = []
    for sp in speakers:
        try:
            name = sp.player_name
        except Exception:
            continue
        available.append(name)
        if name.lower() == room_lower:
            matches.append(sp)
    if len(matches) == 1:
        speaker = matches[0]
        log.debug(
            "Found speaker: %s at %s",
            speaker.player_name,
            speaker.ip_address,
        )
        return speaker
    if len(matches) > 1:
        raise RuntimeError(
            f"Multiple Sonos speakers matched room name {room!r}."
        )
    raise RuntimeError(
        f"No Sonos speaker found with room name {room!r}. "
        f"Available: {available}"
    )


# ---------------------------------------------------------------------------
# LAN IP routing
# ---------------------------------------------------------------------------

def local_ip_toward(remote_ip: str) -> str:
    """Return the local interface IP that can reach *remote_ip*."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((remote_ip, 80))
        return s.getsockname()[0]


# ---------------------------------------------------------------------------
# Ephemeral HTTP server
# ---------------------------------------------------------------------------

# Handler poll interval (seconds) — how often serve_forever wakes to check
# _BaseServer.__shutdown_request.  Keeps shutdown() bounded even if a
# connection is slow; must be short enough not to add noticeable latency.
_SERVER_POLL_INTERVAL = 0.5

# Per-connection read/write timeout (seconds).  Prevents a stalled Sonos
# connection from blocking the handler thread indefinitely.
_CONN_TIMEOUT = 10


class _AnnouncementHandler(http.server.BaseHTTPRequestHandler):
    """
    Serve one capability path to one target IP; deny everything else.

    Query strings are ignored only after matching the authorized path.
    """

    mp3_path: Path
    announcement_path: str
    allowed_client_ip: str
    active: threading.Event

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
        # BaseHTTPRequestHandler includes the request path in its access log.
        # That path is a bearer capability, including for unsuccessful requests.
        pass

    def _serve_audio(self, head_only: bool) -> None:
        request_path = urllib.parse.urlsplit(self.path).path
        if (not self.active.is_set()
                or self.client_address[0] != self.allowed_client_ip
                or request_path != self.announcement_path):
            self.send_error(404, "Not Found")
            return
        try:
            data = b"" if head_only else self.mp3_path.read_bytes()
            size = self.mp3_path.stat().st_size if head_only else len(data)
        except OSError:
            log.warning("HTTP: could not read announcement")
            self.send_error(500, "Internal Server Error")
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                log.debug("HTTP: client disconnected early (ignored)")

    def do_GET(self) -> None:  # noqa: N802
        self._serve_audio(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve_audio(head_only=True)


@contextlib.contextmanager
def serve_file(file_path: Path, bind_ip: str, allowed_client_ip: str):
    """
    Context manager: serve *file_path* over HTTP on a random port and yield
    the URL. Only GET/HEAD with the fresh capability and target IP succeed.

    Shutdown is bounded:
    - ``ThreadingHTTPServer`` with ``daemon_threads=True`` means no in-flight
      handler thread can block process exit.
    - ``server_poll_interval`` caps how long ``shutdown()`` waits between
      checking the shutdown flag, so it returns within ~0.5 s of being called.
    - Each accepted socket gets a short read/write timeout via
      ``finish_request`` override, preventing stalled Sonos connections from
      holding threads open past shutdown.
    - ``server_close()`` is called after join to release the OS socket.
    """

    client_ip = str(ipaddress.IPv4Address(allowed_client_ip))
    path = f"/{secrets.token_urlsafe(32)}/announcement.mp3"
    active = threading.Event()
    active.set()

    class _Handler(_AnnouncementHandler):
        mp3_path = file_path
        announcement_path = path
        allowed_client_ip = client_ip

    _Handler.active = active

    class _BoundedServer(http.server.ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            log.warning("HTTP: announcement request failed")

        def finish_request(self, request, client_address):
            request.settimeout(_CONN_TIMEOUT)
            super().finish_request(request, client_address)

    server = _BoundedServer((bind_ip, 0), _Handler)
    port = server.server_address[1]
    url = f"http://{bind_ip}:{port}{path}"
    log.debug("HTTP server on %s:%d", bind_ip, port)

    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": _SERVER_POLL_INTERVAL},
        daemon=True,
    )
    thread.start()
    try:
        yield url
    finally:
        active.clear()             # revoke even on already accepted connections
        server.shutdown()          # signals serve_forever to stop; bounded by poll_interval
        thread.join(timeout=5)     # daemonic, so won't block exit if it somehow lingers
        server.server_close()      # release the listening socket
        log.debug("HTTP server stopped")


# ---------------------------------------------------------------------------
# Sonos playback helpers
# ---------------------------------------------------------------------------

def _check_source_uri(uri) -> None:
    if not isinstance(uri, str):
        raise SonosSayError("SONOS_SOURCE_UNSUPPORTED")
    if uri.startswith("x-rincon-queue:") and uri.rsplit("#", 1)[-1] != "0":
        raise SonosSayError("SONOS_SOURCE_UNSUPPORTED")


def _check_supported_sources(topology, isolated=False) -> None:
    owners = [topology.speaker]
    if not isolated and topology.coordinator is not topology.speaker:
        owners.append(topology.coordinator)
    for owner in owners:
        try:
            uri = owner.avTransport.GetMediaInfo([("InstanceID", 0)])["CurrentURI"]
        except Exception:
            raise SonosSayError("SONOS_SOURCE_UNSUPPORTED") from None
        _check_source_uri(uri)


def _threaded_events_backend():
    from soco import config, events
    if config.EVENTS_MODULE is not events:
        raise RuntimeError("Only the threaded SoCo event backend is supported")
    return events


class _TransportEventQueue:
    """Bounded SDK queue sink; retain short clips during blocking Play RPCs."""

    def __init__(self, uri):
        self.uri = uri
        self.lock = threading.Lock()
        self.pending = deque()
        self.started = None
        self.wall_started = None
        self.sid = None
        self.last_seq = None
        self.matching_uri = False
        self.failed = False
        self.playing = False
        self.finished = False
        self.closed = False
        self.active = lambda: True

    def arm(self, sid):
        with self.lock:
            self.pending.clear()
            self.started = time.monotonic()
            self.wall_started = time.time()
            self.sid = sid
            self.matching_uri = False

    def put(self, event, *args, **kwargs):
        # SoCo's send_event calls put() from NOTIFY handler threads. Never
        # block those threads on a full queue, and never retain raw metadata.
        with self.lock:
            if self.closed:
                return
            try:
                seq = int(event.seq)
                if not 0 <= seq <= 0xffffffff:
                    raise ValueError("Invalid sequence")
                if self.started is None:
                    self.last_seq = seq
                    return
                if (not isinstance(event.timestamp, (int, float))
                        or not math.isfinite(event.timestamp)):
                    raise ValueError("Invalid event time")
                if event.sid != self.sid or event.timestamp < self.wall_started:
                    return
                if self.last_seq is not None:
                    expected = 1 if self.last_seq == 0xffffffff else self.last_seq + 1
                    if seq != expected:
                        if seq == self.last_seq or seq < self.last_seq:
                            return
                        self.failed = True
                elif seq not in (0, 1):
                    self.failed = True
                self.last_seq = seq
                if seq == 0:  # subscription's initial/prior-source sample
                    return
                values = event.variables
                uri = values.get("av_transport_uri", values.get("current_track_uri"))
                if uri is not None:
                    self.matching_uri = uri == self.uri
                state = values.get("transport_state")
                if state is None:
                    return
                if state not in ("PLAYING", "STOPPED", "TRANSITIONING", "PAUSED_PLAYBACK"):
                    self.failed = True
                    return
                if len(self.pending) >= 64:
                    self.failed = True
                    return
                self.pending.append((time.monotonic(), state, self.matching_uri))
            except Exception:
                self.failed = True

    def observe(self, start_timeout, timeout):
        with self.lock:
            if self.started is None or not self.active():
                self.failed = True
                return False, False
            while self.pending:
                when, state, matching = self.pending.popleft()
                if not matching:
                    continue
                if when >= self.started + timeout:
                    self.failed = True
                if state == "PLAYING":
                    if not self.playing and when >= self.started + start_timeout:
                        self.failed = True
                    self.playing = True
                elif state == "STOPPED":
                    if self.playing:
                        self.finished = True
                elif state != "TRANSITIONING":
                    self.failed = True
            return self.playing, self.finished

    def close(self):
        with self.lock:
            self.closed = True
            self.pending.clear()


class _TransportEvents:
    """Own only this AVTransport subscription, never somebody else's listener."""

    def __init__(self, speaker, uri):
        self.speaker = speaker
        self.queue = _TransportEventQueue(uri)
        self.subscription = None
        self.backend = None
        self.listener = None
        self.closed = False

    def start(self):
        self.backend = _threaded_events_backend()

        class OwnedListener(self.backend.EventListener):
            def stop_listening(self, address):
                thread = self._listener_thread
                thread.stop()
                # SDK 0.31.2's unbounded dummy GET can hang if the listener
                # exits before accepting it. Bound the wake-up and close the
                # socket; this listener belongs solely to our subscription.
                try:
                    with urllib.request.urlopen(
                            f"http://{address[0]}:{address[1]}/", timeout=1):
                        pass
                except urllib.error.HTTPError as exc:
                    exc.close()
                except (OSError, urllib.error.URLError):
                    pass
                finally:
                    thread.join(timeout=3)
                    thread.server.server_close()
                if thread.is_alive():
                    raise RuntimeError("Event listener did not stop")

        self.listener = OwnedListener()
        self.subscription = self.backend.Subscription(
            self.speaker.avTransport, event_queue=self.queue)
        # SDK NOTIFY handlers use the shared SID registry, but each subscription
        # may own a separate listener. No global listener or backend is replaced.
        self.subscription.event_listener = self.listener
        self.subscription.subscribe(requested_timeout=86400, auto_renew=False)
        self.queue.active = lambda: (self.subscription.is_subscribed is True
                                     and self.subscription.time_left > 0)

    def arm(self):
        self.queue.arm(self.subscription.sid)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.queue.close()
        if self.subscription is None:
            return
        try:
            self.subscription.unsubscribe()
        finally:
            # The SDK stops a listener only when the global SID registry is
            # empty. Our private listener must also stop while others remain,
            # or when BaseException bypassed SDK subscription cancellation.
            self.listener.stop()
            thread = self.listener._listener_thread
            if thread is not None and thread.server.fileno() != -1:
                if thread.is_alive():  # interruption during listener startup
                    self.listener.stop_listening(thread.server.server_address)
                else:
                    thread.server.server_close()

    def __enter__(self):
        try:
            self.start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *args):
        self.close()


def wait_for_playback(
    speaker: soco.SoCo,
    timeout: float,
    start_timeout: float = PLAYBACK_START_TIMEOUT,
    events: Optional[_TransportEventQueue] = None,
) -> bool:
    """
    Require observed PLAYING followed by STOPPED within finite deadlines.

    TRANSITIONING does not prove playback started; lost telemetry, pausing,
    unexpected states, and deadline expiry are failures, never completion.
    """
    _validate_timeout(timeout)
    _validate_timeout(start_timeout)

    def _get_state() -> Optional[str]:
        try:
            state = speaker.get_current_transport_info()["current_transport_state"]
            return state if isinstance(state, str) else None
        except Exception:
            return None

    started = events.started if events is not None else time.monotonic()
    overall_deadline = started + timeout
    start_deadline = min(started + start_timeout, overall_deadline)
    seen_playing = False
    if events is not None:
        while time.monotonic() < overall_deadline:
            observed, finished = events.observe(start_timeout, timeout)
            if events.failed:
                return False
            seen_playing = seen_playing or observed
            if finished:
                return time.monotonic() < overall_deadline
            state = _get_state()
            if time.monotonic() >= overall_deadline:
                return False
            if state not in ("PLAYING", "STOPPED", "TRANSITIONING"):
                return False
            # SOAP samples must also be tied to this attempt's URI. STOPPED
            # before a correlated start never proves delivery.
            if state == "PLAYING":
                if not seen_playing and time.monotonic() >= start_deadline:
                    return False
                try:
                    uri = speaker.avTransport.GetMediaInfo([("InstanceID", 0)])["CurrentURI"]
                except Exception:
                    return False
                observed_at = time.monotonic()
                if (uri != events.uri or observed_at >= overall_deadline
                        or (not seen_playing and observed_at >= start_deadline)):
                    return False
                seen_playing = True
            elif state == "STOPPED" and seen_playing:
                try:
                    uri = speaker.avTransport.GetMediaInfo([("InstanceID", 0)])["CurrentURI"]
                except Exception:
                    return False
                return uri == events.uri and time.monotonic() < overall_deadline
            if not seen_playing and time.monotonic() >= start_deadline:
                return False
            deadline = overall_deadline if seen_playing else start_deadline
            time.sleep(max(0, min(0.25, deadline - time.monotonic())))
        return False
    while time.monotonic() < start_deadline:
        state = _get_state()
        if state == "PLAYING":
            seen_playing = True
            break
        if state not in ("STOPPED", "TRANSITIONING"):
            return False
        time.sleep(max(0, min(0.25, start_deadline - time.monotonic())))

    if not seen_playing:
        return False

    while time.monotonic() < overall_deadline:
        state = _get_state()
        if time.monotonic() >= overall_deadline:
            return False
        if state == "STOPPED":
            return True
        if state not in ("PLAYING", "TRANSITIONING"):
            return False
        time.sleep(max(0, min(0.5, overall_deadline - time.monotonic())))
    return False


class _MutationSignalGuard:
    """Handle signals from live preflight through temporary-resource cleanup."""

    def __init__(self) -> None:
        self.cleaning = False
        self.deferred: list[BaseException] = []
        self._previous = {}

    def install(self) -> None:
        handled = [signal.SIGINT]
        if hasattr(signal, "SIGTERM"):
            handled.append(signal.SIGTERM)
        self._previous = {
            signum: signal.getsignal(signum)
            for signum in handled
        }
        installed = []
        try:
            for signum in handled:
                signal.signal(signum, self._handle)
                installed.append(signum)
        except BaseException:
            for signum in installed:
                signal.signal(signum, self._previous[signum])
            raise

    def begin_cleanup(self) -> None:
        self.cleaning = True

    def restore(self) -> None:
        for signum, handler in self._previous.items():
            signal.signal(signum, handler)

    def _handle(self, signum: int, frame: object) -> None:  # noqa: ARG002
        interrupt: BaseException
        if signum == signal.SIGINT:
            interrupt = KeyboardInterrupt()
        else:
            interrupt = SystemExit(143)
        if self.cleaning:
            self.deferred.append(interrupt)
            return
        raise interrupt


def _run_cleanup_step(
    action,
    interrupts: list[BaseException],
    warning: str,
) -> bool:
    """Run each action once; the lifetime signal guard defers real signals."""
    try:
        action()
        return True
    except (KeyboardInterrupt, SystemExit) as exc:
        interrupts.append(exc)
    except Exception:
        pass
    log.warning(warning)
    return False


def _clear_temporary_uri(speaker: soco.SoCo) -> None:
    """Clear an announcement URI when the saved transport URI was empty."""
    transport_failed = False
    try:
        state = speaker.get_current_transport_info()["current_transport_state"]
        if state in ("PLAYING", "TRANSITIONING", "PAUSED_PLAYBACK"):
            speaker.stop()
        elif state not in ("STOPPED", "NO_MEDIA_PRESENT"):
            transport_failed = True
    except Exception:
        transport_failed = True
    # Even when telemetry/stop fails, still attempt to clear the temporary URI.
    speaker.avTransport.SetAVTransportURI(
        [
            ("InstanceID", 0),
            ("CurrentURI", ""),
            ("CurrentURIMetaData", ""),
        ]
    )
    if transport_failed:
        raise RuntimeError("Could not verify transport before clearing URI")


def _restore_after_announcement(
    speaker: soco.SoCo,
    snapshot: Optional[Snapshot],
    snapshot_taken: bool,
    original_volume: Optional[int],
    topology,
    topology_mutated: bool,
    state_mutated: bool = True,
) -> tuple[bool, Optional[BaseException]]:
    """Attempt safe restoration once under the caller's cleanup signal guard."""
    interrupts: list[BaseException] = []
    restore_failed = False
    uri_cleared = False
    if not state_mutated:
        # Snapshot reads alone create no transport/rendering recovery duty.
        # An attempted unjoin still requires exact group recovery, and only that.
        if topology_mutated:
            restored = _run_cleanup_step(
                lambda: restore_target_group(topology), interrupts,
                "Could not restore Sonos group",
            )
            return not restored, interrupts[0] if interrupts else None
        return False, None

    local_safe = transport_safe = True
    if topology is not None:
        local_safe = _run_cleanup_step(
            lambda: verify_target_identity(topology), interrupts,
            "Could not verify target identity for restoration",
        )
        transport_safe = local_safe and _run_cleanup_step(
            lambda: verify_isolated_target(topology, TOPOLOGY_VERIFY_TIMEOUT),
            interrupts, "Could not verify room for transport restoration",
        )
        restore_failed = not transport_safe

    if snapshot_taken and snapshot is not None:
        restored = False
        if transport_safe:
            restored = _run_cleanup_step(
                lambda: snapshot.restore(fade=False),
                interrupts,
                "Could not restore transport state",
            )
            restore_failed = restore_failed or not restored
            if topology is not None:
                # One post-restore sample also guards the next transport write
                # (empty-URI clearing); do not poll a third time for that case.
                transport_safe = _run_cleanup_step(
                    lambda: verify_isolated_target(topology, TOPOLOGY_VERIFY_TIMEOUT),
                    interrupts, "Could not verify restored room topology",
                )
                restore_failed = restore_failed or not transport_safe
        if (topology is not None and local_safe
                and (not restored or not transport_safe)):
            # A failed/skipped restore or post-check can outlive an IP change.
            # Local fallback needs new identity proof independent of topology.
            local_safe = _run_cleanup_step(
                lambda: verify_target_identity(topology), interrupts,
                "Could not verify target identity for local restoration",
            )
            restore_failed = restore_failed or not local_safe
        if not restored and local_safe:
            # Snapshot already restored mute on success; do not write it twice.
            mute_restored = _run_cleanup_step(
                lambda: setattr(speaker, "mute", snapshot.mute), interrupts,
                "Could not restore mute",
            )
            restore_failed = restore_failed or not mute_restored

        saved_uri = getattr(snapshot, "media_uri", None) or ""
        should_clear = not saved_uri and not topology_mutated and transport_safe
        if should_clear and topology is None:
            # Compatibility for internal callers without a captured room plan.
            try:
                coordinator = speaker.is_coordinator
                if not isinstance(coordinator, bool):
                    raise RuntimeError("Coordinator state is unavailable")
                should_clear = coordinator
            except (KeyboardInterrupt, SystemExit) as exc:
                interrupts.append(exc)
                restore_failed = True
                should_clear = False
            except Exception:
                restore_failed = True
                should_clear = False
        if should_clear:
            uri_cleared = _run_cleanup_step(
                lambda: _clear_temporary_uri(speaker),
                interrupts,
                "Could not clear temporary AV transport URI",
            )
            restore_failed = restore_failed or not uri_cleared

    if original_volume is not None and local_safe:
        volume_restored = _run_cleanup_step(
            lambda: setattr(speaker, "volume", original_volume),
            interrupts,
            "Could not restore volume",
        )
        restore_failed = restore_failed or not volume_restored

    if topology_mutated and local_safe:
        group_restored = _run_cleanup_step(
            lambda: restore_target_group(topology),
            interrupts,
            "Could not restore Sonos group",
        )
        restore_failed = restore_failed or not group_restored

    if snapshot_taken and snapshot is not None and local_safe:
        try:
            if speaker.mute != snapshot.mute:
                raise RuntimeError("Mute mismatch")
            # GetMediaInfo reports queue containers, unlike track-info URI.
            # Regrouping replaces detached transport; do not compare that
            # inherited source to an isolated target's empty snapshot URI.
            if snapshot.media_uri and not topology_mutated and transport_safe:
                media = speaker.avTransport.GetMediaInfo([("InstanceID", 0)])
                if media["CurrentURI"] != snapshot.media_uri:
                    raise RuntimeError("Source mismatch")
                state = speaker.get_current_transport_info()["current_transport_state"]
                if state != snapshot.transport_state:
                    raise RuntimeError("Transport mismatch")
        except (KeyboardInterrupt, SystemExit) as exc:
            interrupts.append(exc)
            restore_failed = True
        except Exception:
            log.warning("Could not verify restored mute/source/transport")
            restore_failed = True

    if original_volume is not None and local_safe:
        try:
            if speaker.volume != original_volume:
                log.warning("Volume mismatch after restore")
                restore_failed = True
        except (KeyboardInterrupt, SystemExit) as exc:
            interrupts.append(exc)
            restore_failed = True
        except Exception:
            log.warning("Could not verify restored volume")
            restore_failed = True
    if uri_cleared:
        try:
            if speaker.get_current_track_info()["uri"] != "":
                log.warning("AV transport URI still set after clear")
                restore_failed = True
        except (KeyboardInterrupt, SystemExit) as exc:
            interrupts.append(exc)
            restore_failed = True
        except Exception:
            log.warning("Could not verify cleared transport URI")
            restore_failed = True

    pending_interrupt = interrupts[0] if interrupts else None
    return restore_failed, pending_interrupt


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def _required_setting(value: Optional[str], name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Set {name}.")
    return value


def _validate_timeout(value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Timeout must be finite and positive.")


def announce(
    text: str,
    room: Optional[str] = None,
    volume: int = DEFAULT_VOLUME,
    model: str = DEFAULT_MODEL,
    voice_id: Optional[str] = None,
    timeout: Optional[float] = DEFAULT_TIMEOUT,
    dry_run: bool = False,
    stability: Optional[str] = None,
    tts_timeout: float = DEFAULT_TTS_TIMEOUT,
) -> None:
    """Full pipeline: validate, discover, synthesise, isolate, play, restore."""

    # --- validate text ---
    if not text:
        raise ValueError("Text must not be empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(
            f"Text is {len(text)} characters; maximum is {MAX_TEXT_CHARS}."
        )

    # Explicit blank values fail instead of silently falling back to environment.
    room = _required_setting(
        room if room is not None else os.environ.get("SONOS_ROOM"),
        "--room or SONOS_ROOM",
    )
    voice_id = _required_setting(
        voice_id if voice_id is not None else os.environ.get("ELEVENLABS_VOICE_ID"),
        "--voice-id or ELEVENLABS_VOICE_ID",
    )
    if timeout is not None:
        _validate_timeout(timeout)
    _validate_timeout(tts_timeout)
    if not 0 <= volume <= 100:
        raise ValueError("Volume must be between 0 and 100.")

    # --- validate ElevenLabs identifiers ---
    _validate_elevenlabs_id(voice_id, "voice-id")
    _validate_elevenlabs_id(model, "model")
    resolved_stability = resolve_stability(model, stability)

    if dry_run:
        stability_text = (
            f"{resolved_stability.name} ({resolved_stability.value})"
            if resolved_stability is not None
            else "legacy (unset)"
        )
        print(
            f"[dry-run] Would announce to {room!r} at volume {volume}: "
            f"{text!r}; model={model!r}, stability={stability_text}"
        )
        return

    service = _required_setting(
        os.environ.get("ELEVENLABS_KEYCHAIN_SERVICE"),
        "ELEVENLABS_KEYCHAIN_SERVICE",
    )
    account = _required_setting(
        os.environ.get("ELEVENLABS_KEYCHAIN_ACCOUNT"),
        "ELEVENLABS_KEYCHAIN_ACCOUNT",
    )
    speaker = topology = None
    snapshot: Optional[Snapshot] = None
    snapshot_taken = False
    original_volume: Optional[int] = None
    topology_mutated = False
    state_mutated = False
    pending_error: Optional[BaseException] = None
    cleanup_interrupt: Optional[BaseException] = None
    restore_failed = False
    signal_guard = _MutationSignalGuard()

    def arm_group_recovery() -> None:
        nonlocal topology_mutated
        topology_mutated = True

    def begin_cleanup() -> None:
        if signal_guard.cleaning:
            return
        try:
            signal_guard.begin_cleanup()
        except (KeyboardInterrupt, SystemExit) as exc:
            signal_guard.deferred.append(exc)
            signal_guard.cleaning = True

    signal_guard.install()
    try:
        # Keep the guard installed until ExitStack has deleted temporary audio.
        # Evaluate deferred signals and restoration failures only afterward.
        with contextlib.ExitStack() as resources:
            try:
                log.info("Discovering Sonos speaker in room %r…", room)
                try:
                    speaker = discover_speaker(room)
                    local_ip = local_ip_toward(speaker.ip_address)
                except Exception as exc:
                    raise SonosSayError("SONOS_DISCOVERY_FAILED") from exc
                topology = validate_announcement_target(speaker)
                _check_supported_sources(topology)

                try:
                    api_key = get_api_key(service, account)
                except Exception as exc:
                    raise SonosSayError("ELEVENLABS_KEY_FAILED") from exc

                tmpdir = resources.enter_context(
                    tempfile.TemporaryDirectory(prefix="sonos_say_")
                )
                mp3_path = Path(tmpdir) / "announcement.mp3"
                log.info("Synthesising speech via ElevenLabs…")
                try:
                    synthesise_mp3(
                        text, voice_id, model, api_key, mp3_path,
                        stability=resolved_stability, timeout=tts_timeout,
                    )
                    mp3_bytes = mp3_path.stat().st_size
                    if mp3_bytes <= 0:
                        raise RuntimeError("Empty synthesized audio")
                    playback_timeout = (timeout if timeout is not None
                                        else math.ceil(mp3_bytes / 16000) + 30)
                except Exception as exc:
                    raise SonosSayError("ELEVENLABS_SYNTHESIS_FAILED") from exc
                finally:
                    del api_key

                _check_supported_sources(topology)
                if topology.detach:
                    detach_target(topology, before_unjoin=arm_group_recovery)

                # Synthesis can outlive a topology change. Recheck ownership
                # before even capturing state, including an already bonded room.
                verify_isolated_target(topology)
                snapshot = Snapshot(speaker)
                snapshot.snapshot()
                _check_source_uri(snapshot.media_uri or "")
                snapshot_taken = True
                original_volume = speaker.volume

                with serve_file(mp3_path, local_ip, speaker.ip_address) as mp3_url:
                    recorder = _TransportEvents(speaker, mp3_url)
                    try:
                        recorder.start()
                        log.info("Serving announcement to the target speaker")
                        verify_isolated_target(topology)
                        try:
                            _check_supported_sources(topology, isolated=True)
                        except SonosSayError:
                            # Source was never replaced: only regroup, never invoke
                            # Snapshot's unsupported cloud restore/play path.
                            snapshot_taken = False
                            original_volume = None
                            raise
                        # A rendering RPC can apply its change and then fail.
                        state_mutated = True
                        speaker.volume = volume
                        speaker.mute = False
                        recorder.arm()
                        speaker.play_uri(mp3_url, title="Announcement")
                        if not wait_for_playback(speaker, playback_timeout, events=recorder.queue):
                            raise SonosSayError("SONOS_PLAYBACK_FAILED")
                    finally:
                        # Deferral covers event/server shutdown and restoration.
                        begin_cleanup()
                        recorder.close()
            except BaseException as exc:
                pending_error = exc
            finally:
                begin_cleanup()
                restore_failed, cleanup_interrupt = _restore_after_announcement(
                    speaker, snapshot, snapshot_taken, original_volume,
                    topology, topology_mutated, state_mutated=state_mutated,
                )
    finally:
        signal_guard.restore()

    if signal_guard.deferred:
        cleanup_interrupt = signal_guard.deferred[0]
    if restore_failed:
        raise SonosSayError("SONOS_RESTORE_FAILED") from (
            pending_error or cleanup_interrupt
        )
    if cleanup_interrupt is not None:
        raise cleanup_interrupt
    if pending_error is not None:
        if isinstance(pending_error, (KeyboardInterrupt, SystemExit, SonosSayError)):
            raise pending_error
        if isinstance(pending_error, TopologyError):
            raise SonosSayError("SONOS_TOPOLOGY_FAILED") from pending_error
        raise SonosSayError("SONOS_PLAYBACK_FAILED") from pending_error


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sonos-say",
        description="Announce text on a Sonos speaker via ElevenLabs TTS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  sonos-say "Dinner is ready!"\n'
            '  echo "Hello" | sonos-say -\n'
            '  sonos-say --room "Office" --volume 44 "Important alert"\n'
        ),
    )
    p.add_argument(
        "text",
        nargs="?",
        metavar="TEXT",
        help='Text to speak. Use "-" to read from stdin.',
    )
    p.add_argument(
        "--room",
        default=os.environ.get("SONOS_ROOM"),
        help="Sonos room name (or set SONOS_ROOM; no default).",
    )
    p.add_argument(
        "--volume",
        type=int,
        default=DEFAULT_VOLUME,
        metavar="PCT",
        help=f"Announcement volume 0-100 (default: {DEFAULT_VOLUME}).",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"ElevenLabs model ID (default: {DEFAULT_MODEL!r}).",
    )
    p.add_argument(
        "--stability",
        choices=tuple(STABILITY_PRESETS),
        help=(
            "ElevenLabs v3 stability preset (default: natural for v3)."
        ),
    )
    p.add_argument(
        "--voice-id",
        default=os.environ.get("ELEVENLABS_VOICE_ID"),
        dest="voice_id",
        help="ElevenLabs voice ID (or set ELEVENLABS_VOICE_ID; no default).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="SECS",
        help="Hard playback timeout override (default: ceil(MP3 bytes / 16000) + 30).",
    )
    p.add_argument(
        "--tts-timeout", type=float, default=DEFAULT_TTS_TIMEOUT, metavar="SECS",
        help="Independent synthesis/header/body timeout (default: 180 seconds).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be announced without touching Sonos or ElevenLabs.",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # SoCo debug logs contain SOAP payloads, including the capability URL.
    # Enable debug only for our own fixed/sanitized diagnostics, not the root.
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    log.setLevel(logging.DEBUG if args.debug else logging.INFO)

    # Resolve text from positional arg or stdin
    raw_text: Optional[str] = args.text
    if raw_text is None or raw_text == "-":
        if sys.stdin.isatty() and raw_text is None:
            parser.error("Provide TEXT as a positional argument or pipe it via stdin.")
        raw_text = sys.stdin.read()

    text = raw_text.strip()
    if not text:
        parser.error("Text must not be blank.")

    # Volume range
    if not (0 <= args.volume <= 100):
        parser.error(f"--volume must be between 0 and 100, got {args.volume}.")

    # Timeout
    if args.timeout is not None and (not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error("--timeout must be finite and positive.")
    if not math.isfinite(args.tts_timeout) or args.tts_timeout <= 0:
        parser.error("--tts-timeout must be finite and positive.")

    try:
        announce(
            text=text,
            room=args.room,
            volume=args.volume,
            model=args.model,
            voice_id=args.voice_id,
            timeout=args.timeout,
            dry_run=args.dry_run,
            stability=args.stability,
            tts_timeout=args.tts_timeout,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except SonosSayError as exc:
        print(f"SONOS_SAY_ERROR={exc.code}", file=sys.stderr)
        return 2
    except RuntimeError:
        log.debug("Unclassified runtime error")
        print("SONOS_SAY_ERROR=SONOS_PLAYBACK_FAILED", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001
        log.debug("Unexpected error")
        print("SONOS_SAY_ERROR=SONOS_PLAYBACK_FAILED", file=sys.stderr)
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
