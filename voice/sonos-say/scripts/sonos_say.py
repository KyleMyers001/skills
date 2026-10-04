#!/usr/bin/env python3
"""
sonos-say — TTS announcement to a Sonos speaker via ElevenLabs + SoCo.

Pipeline:
  1. Fetch ElevenLabs API key from macOS Keychain
  2. Synthesise text → temp MP3 via ElevenLabs REST API
  3. Discover target Sonos speaker via multicast UPnP
  4. Snapshot current transport state + volume
  5. Spin up a short-lived local HTTP server to serve the MP3
  6. Issue Sonos play_uri, wait for completion (or timeout)
  7. Restore state + volume, stop server, delete temporary files

Usage:
    sonos-say "Hello, world!"
    echo "Hello" | sonos-say -
    sonos-say --room "Test Room" --volume 44 "Text here"
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import ipaddress
import logging
import math
import os
import re
import secrets
import signal
import socket
import subprocess
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

from elevenlabs_voice import (  # noqa: E402
    DEFAULT_MODEL_ID,
    STABILITY_PRESETS,
    StabilityPreset,
    resolve_stability,
    validate_model_id,
)
from sonos_topology import (  # noqa: E402
    TopologyError,
    detach_target,
    restore_target_group,
    validate_announcement_target,
    verify_isolated_target,
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
DEFAULT_TIMEOUT = 120          # seconds to wait for playback to complete
MAX_TEXT_CHARS = 5_000         # implementation limit; keep speech short
# Maximum seconds to wait for transport to enter PLAYING before
# accepting that playback never started (avoids restoring too early on a slow
# Sonos transition).
PLAYBACK_START_TIMEOUT = 8.0
ELEVENLABS_TTS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
OUTPUT_FORMAT = "mp3_44100_128"

# Strict allowlist for ElevenLabs voice identifiers.
# Letters, digits, underscores, and hyphens; 1-64 characters.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

log = logging.getLogger("sonos_say")

ERROR_CODES = frozenset({
    "ELEVENLABS_KEY_FAILED",
    "ELEVENLABS_SYNTHESIS_FAILED",
    "SONOS_DISCOVERY_FAILED",
    "SONOS_PLAYBACK_FAILED",
    "SONOS_RESTORE_FAILED",
    "SONOS_TOPOLOGY_FAILED",
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
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(
            f"Invalid {label}. "
            "Must contain only letters, digits, underscores, or hyphens "
            "and be 1–64 characters long."
        )
    return value


# ---------------------------------------------------------------------------
# Keychain
# ---------------------------------------------------------------------------

def get_api_key(service: str, account: str) -> str:
    """Retrieve ElevenLabs API key from macOS Keychain. Never prints the value."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            check=True,
        )
        key = result.stdout.strip()
        if not key:
            raise RuntimeError("Keychain returned empty value")
        return key
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Could not retrieve API key from Keychain") from exc


# ---------------------------------------------------------------------------
# ElevenLabs TTS
# ---------------------------------------------------------------------------

class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", {}, fp)


def _tts_opener():
    """Use a private opener so credentials never follow a redirect."""
    return urllib.request.build_opener(_RejectRedirects())


def synthesise_mp3(
    text: str,
    voice_id: str,
    model: str,
    api_key: str,
    dest_path: Path,
    output_format: str = OUTPUT_FORMAT,
    stability: Optional[StabilityPreset] = None,
) -> None:
    """
    Call ElevenLabs REST API and write the MP3 to *dest_path*.

    *voice_id* is percent-encoded before URL insertion.
    *model* is validated but only used in the JSON body (no URL insertion).
    The API key is never logged.
    """
    import json as _json

    # URL-encode voice_id for safe path insertion; model goes into JSON only.
    encoded_voice_id = urllib.parse.quote(voice_id, safe="")
    url = (
        f"{ELEVENLABS_TTS_BASE}/{encoded_voice_id}"
        f"?output_format={urllib.parse.quote(output_format, safe='')}"
    )
    payload_data = {"text": text, "model_id": model}
    if stability is not None:
        payload_data["voice_settings"] = {
            "stability": stability.value,
        }
    payload = _json.dumps(payload_data).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    try:
        with _tts_opener().open(req, timeout=30) as resp:
            if resp.status != 200:
                raise RuntimeError("ElevenLabs request failed")
            data = resp.read()
    except urllib.error.HTTPError as exc:
        exc.close()
        raise RuntimeError("ElevenLabs request failed") from None
    except urllib.error.URLError:
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
# SIGTERM handling
# ---------------------------------------------------------------------------

def _install_sigterm_handler() -> None:
    """
    Convert SIGTERM into SystemExit(143) so that finally blocks—including
    Sonos state restoration—run before the process exits.

    SIGTERM is not available on Windows; we install silently on POSIX only.
    Only installed once (idempotent via SIG_IGN check is skipped; we always
    set our handler and let the caller decide).
    """
    if not hasattr(signal, "SIGTERM"):
        return

    def _handler(signum: int, frame: object) -> None:  # noqa: ARG001
        log.warning("Received SIGTERM — cleaning up…")
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, _handler)


# ---------------------------------------------------------------------------
# Sonos playback helpers
# ---------------------------------------------------------------------------

def wait_for_playback(
    speaker: soco.SoCo,
    timeout: float,
    start_timeout: float = PLAYBACK_START_TIMEOUT,
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

    overall_deadline = time.monotonic() + timeout
    start_deadline = min(time.monotonic() + start_timeout, overall_deadline)
    seen_playing = False
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


@contextlib.contextmanager
def _defer_cleanup_signals():
    """Record SIGINT/SIGTERM instead of interrupting restoration."""
    deferred: list[BaseException] = []
    handled = [signal.SIGINT]
    if hasattr(signal, "SIGTERM"):
        handled.append(signal.SIGTERM)
    previous = {
        signum: signal.getsignal(signum)
        for signum in handled
    }

    def _handler(signum: int, frame: object) -> None:  # noqa: ARG001
        if signum == signal.SIGINT:
            deferred.append(KeyboardInterrupt())
        else:
            deferred.append(SystemExit(143))

    for signum in handled:
        signal.signal(signum, _handler)
    try:
        yield deferred
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


class _MutationSignalGuard:
    """Handle signals from before mutation through cleanup handler entry."""

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
    """Run one cleanup action, retrying once after an interrupt."""
    for attempt in range(2):
        try:
            action()
            return True
        except (KeyboardInterrupt, SystemExit) as exc:
            interrupts.append(exc)
            if attempt == 0:
                continue
        except Exception:
            pass
        log.warning(warning)
        return False
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
) -> tuple[bool, Optional[BaseException]]:
    """Attempt every state and topology restoration under deferred SIGTERM."""
    interrupts: list[BaseException] = []
    restore_failed = False
    uri_cleared = False

    with _defer_cleanup_signals() as deferred:
        if snapshot_taken and snapshot is not None:
            restored = _run_cleanup_step(
                lambda: snapshot.restore(fade=False),
                interrupts,
                "Could not restore transport state",
            )
            restore_failed = restore_failed or not restored

            saved_uri = getattr(snapshot, "media_uri", None) or ""
            try:
                should_clear = False
                if not saved_uri:
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

        if original_volume is not None:
            volume_restored = _run_cleanup_step(
                lambda: setattr(speaker, "volume", original_volume),
                interrupts,
                "Could not restore volume",
            )
            restore_failed = restore_failed or not volume_restored

        if topology_mutated:
            group_restored = _run_cleanup_step(
                lambda: restore_target_group(topology),
                interrupts,
                "Could not restore Sonos group",
            )
            restore_failed = restore_failed or not group_restored

        if original_volume is not None:
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

    interrupts.extend(deferred)

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
    timeout: float = DEFAULT_TIMEOUT,
    dry_run: bool = False,
    stability: Optional[str] = None,
) -> None:
    """Full pipeline: synthesise, discover, serve, play, restore."""

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
    _validate_timeout(timeout)
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
    try:
        api_key = get_api_key(service, account)
    except Exception as exc:
        raise SonosSayError("ELEVENLABS_KEY_FAILED") from exc

    # Work in a temp directory that we own; cleaned up automatically.
    with tempfile.TemporaryDirectory(prefix="sonos_say_") as tmpdir:
        mp3_path = Path(tmpdir) / "announcement.mp3"

        log.info("Synthesising speech via ElevenLabs…")
        try:
            synthesise_mp3(
                text,
                voice_id,
                model,
                api_key,
                mp3_path,
                stability=resolved_stability,
            )
        except Exception as exc:
            raise SonosSayError("ELEVENLABS_SYNTHESIS_FAILED") from exc

        log.info("Discovering Sonos speaker in room %r…", room)
        try:
            speaker = discover_speaker(room)
            local_ip = local_ip_toward(speaker.ip_address)
        except Exception as exc:
            raise SonosSayError("SONOS_DISCOVERY_FAILED") from exc
        log.debug("Local IP toward speaker: %s", local_ip)

        try:
            topology = validate_announcement_target(speaker)
        except TopologyError as exc:
            raise SonosSayError("SONOS_TOPOLOGY_FAILED") from exc

        # All mutable Sonos state is captured and restored inside a single
        # guarded block.  snapshot_taken and original_volume track whether
        # each capture succeeded so that restoration is never attempted
        # without a valid baseline.
        #
        # Interruption policy:
        #   KeyboardInterrupt (Ctrl-C) → cleanup → re-raise KeyboardInterrupt
        #                                → main() returns 130
        #   SystemExit(143)   (SIGTERM) → cleanup → re-raise SystemExit(143)
        #                                → shell/orchestrator sees exit code 143
        #   SystemExit(N!=143)          → cleanup → re-raise SystemExit(N) unchanged
        snapshot: Optional[Snapshot] = None
        snapshot_taken = False
        original_volume: Optional[int] = None
        topology_mutated = False
        pending_error: Optional[BaseException] = None
        cleanup_interrupt: Optional[BaseException] = None
        restore_failed = False
        signal_guard = _MutationSignalGuard()
        signal_guard.install()

        try:
            if topology.detach:
                # Set this before unjoin: a timed-out call may still have
                # changed the household and therefore always requires rejoin.
                topology_mutated = True
                detach_target(topology)

            # A grouped member cannot reliably accept transport snapshot
            # calls. Once isolated it is its own coordinator, while rejoining
            # later restores the original group's transport ownership.
            snapshot = Snapshot(speaker)
            snapshot.snapshot()
            snapshot_taken = True
            log.debug("Snapshot taken")
            original_volume = speaker.volume

            with serve_file(mp3_path, local_ip, speaker.ip_address) as mp3_url:
                log.info("Serving announcement to the target speaker")
                verify_isolated_target(topology)
                speaker.volume = volume
                log.info("Playing announcement…")
                speaker.play_uri(mp3_url, title="Announcement")

                finished = wait_for_playback(speaker, timeout)
                if not finished:
                    raise SonosSayError("SONOS_PLAYBACK_FAILED")
        except BaseException as exc:
            pending_error = exc
        finally:
            try:
                signal_guard.begin_cleanup()
            except (KeyboardInterrupt, SystemExit) as exc:
                # A direct interrupt at the transition cannot be deferred by
                # the handler until cleaning is true. Record it and complete
                # the atomic state transition before any restoration starts.
                signal_guard.deferred.append(exc)
                signal_guard.cleaning = True
            try:
                try:
                    restore_failed, cleanup_interrupt = (
                        _restore_after_announcement(
                            speaker,
                            snapshot,
                            snapshot_taken,
                            original_volume,
                            topology,
                            topology_mutated,
                        )
                    )
                except (KeyboardInterrupt, SystemExit) as exc:
                    # The outer guard is already in cleanup mode. This catches
                    # direct interruption injected at inner handler entry,
                    # before that inner handler can become active.
                    signal_guard.deferred.append(exc)
                    restore_failed, cleanup_interrupt = (
                        _restore_after_announcement(
                            speaker,
                            snapshot,
                            snapshot_taken,
                            original_volume,
                            topology,
                            topology_mutated,
                        )
                    )
            finally:
                signal_guard.restore()

            if signal_guard.deferred:
                cleanup_interrupt = signal_guard.deferred[0]

        if restore_failed:
            cause = pending_error or cleanup_interrupt
            raise SonosSayError("SONOS_RESTORE_FAILED") from cause
        if cleanup_interrupt is not None:
            raise cleanup_interrupt
        if pending_error is not None:
            if isinstance(pending_error, (KeyboardInterrupt, SystemExit)):
                raise pending_error
            if isinstance(pending_error, TopologyError):
                raise SonosSayError(
                    "SONOS_TOPOLOGY_FAILED"
                ) from pending_error
            raise SonosSayError(
                "SONOS_PLAYBACK_FAILED"
            ) from pending_error


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
        help=f"Max seconds to wait for playback (default: {DEFAULT_TIMEOUT}).",
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
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive.")

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
