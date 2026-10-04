#!/usr/bin/env python3
"""
airpods_say.py — Stream ElevenLabs TTS audio to the Mac's current AirPods output.

Usage:
    airpods_say.py [OPTIONS] TEXT
    airpods_say.py [OPTIONS] -

Exit codes:
    0  Completed playback (or successful --dry-run)
    1  General / argument error
    2  AirPods not the current default output
    3  AirPods changed/disconnected mid-playback
    4  Keychain / credential error
    5  ElevenLabs API error
    6  Dependency missing (ffplay)
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import http.client
import json
import os
import re
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TEXT_CHARS = 5000

DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"

ELEVENLABS_STREAM_URL = (
    "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
)

FFPLAY_PRIMARY = "/opt/homebrew/bin/ffplay"

# Strict allowlists — only patterns matching known safe IDs are accepted.
_VOICE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

# ---------------------------------------------------------------------------
# Streaming read tuning
# ---------------------------------------------------------------------------

# Small initial read size so the first audio bytes reach ffplay promptly.
# A 64 KB read on a short (~2–5 KB) TTS response blocks until the entire body
# arrives, causing 10-20 s of silence before first audio.  1 KB is the sweet
# spot: reliably one syscall, still far below typical socket MTU aggregation,
# and the OS will coalesce writes to ffplay's stdin automatically.
STREAM_CHUNK = 1024  # bytes per read iteration

# ---------------------------------------------------------------------------
# Drain phase constants
# ---------------------------------------------------------------------------

# Bitrate of DEFAULT_OUTPUT_FORMAT "mp3_44100_128" in bytes per second.
# Used to estimate audio duration from bytes piped to ffplay, for a
# duration-aware safety timeout in the post-EOF drain phase.
_MP3_128K_BYTES_PER_SEC = 128_000 // 8  # 16_000

# Safety timeout bounds and scale factor for the post-EOF drain phase.
# After stdin EOF, ffplay may still have buffered audio to decode and play.
# We wait up to max(_DRAIN_TIMEOUT_MIN_SECS,
#                  estimated_duration * _DRAIN_TIMEOUT_SCALE)
# seconds, capped at _DRAIN_TIMEOUT_MAX_SECS, before killing a hung player.
_DRAIN_TIMEOUT_MIN_SECS  = 30.0   # minimum wait regardless of audio length
_DRAIN_TIMEOUT_MAX_SECS  = 600.0  # hard ceiling (~10 min): hung-player guard
_DRAIN_TIMEOUT_SCALE     = 2.0    # multiply estimated audio duration by this
_DRAIN_POLL_SECS         = 0.25   # seconds between polls in drain loop

# ---------------------------------------------------------------------------
# system_profiler fast-path for default output device
# ---------------------------------------------------------------------------

# Path to system_profiler; overridable in tests.
SYSTEM_PROFILER_PATH = "/usr/sbin/system_profiler"

# Timeout for the system_profiler fast-path query (seconds).
# system_profiler SPAudioDataType -detailLevel mini typically completes in
# ~0.1–0.3 s.  3 s is generous and orders of magnitude faster than the
# CoreAudio ctypes path (~10 s observed on some machines due to Bluetooth
# device enumeration).
SYSTEM_PROFILER_TIMEOUT = 3

# ---------------------------------------------------------------------------
# CoreAudio FourCC selector constants (from <CoreAudio/AudioHardwareBase.h>)
# ---------------------------------------------------------------------------
# kAudioObjectSystemObject is always device ID 1.
_kAudioObjectSystemObject            = 1
# 'dOut' — kAudioHardwarePropertyDefaultOutputDevice
_kAudioHardwarePropertyDefaultOutputDevice = 0x644F_7574
# 'glob' — kAudioObjectPropertyScopeGlobal
_kAudioObjectPropertyScopeGlobal     = 0x676C_6F62
# kAudioObjectPropertyElementMain (was kAudioObjectPropertyElementMaster)
_kAudioObjectPropertyElementMain     = 0
# 'lnam' — kAudioObjectPropertyName
_kAudioObjectPropertyName            = 0x6C6E_616D

# ---------------------------------------------------------------------------
# CoreAudio helper (macOS-only)
# ---------------------------------------------------------------------------

class CoreAudioError(Exception):
    pass


def _load_coreaudio() -> ctypes.CDLL:
    """Load the CoreAudio framework via ctypes."""
    path = ctypes.util.find_library("CoreAudio")
    if not path:
        raise CoreAudioError("CoreAudio library not found (non-macOS?)")
    return ctypes.cdll.LoadLibrary(path)


def _get_default_output_device_name_coreaudio() -> str:
    """
    Return the display name of the macOS default output audio device via
    the CoreAudio ctypes API.

    Uses AudioObjectGetPropertyData with
    kAudioHardwarePropertyDefaultOutputDevice, then kAudioObjectPropertyName.

    Raises CoreAudioError on any failure.

    This is the fallback implementation used when the faster system_profiler
    primary path is unavailable (missing binary, timeout, invalid JSON, etc.).
    Call get_default_output_device_name() for the primary+fallback path.
    """
    try:
        ca = _load_coreaudio()
    except OSError as exc:
        raise CoreAudioError(f"Cannot load CoreAudio: {exc}") from exc

    # AudioObjectID is UInt32
    kAudioObjectSystemObject = ctypes.c_uint32(_kAudioObjectSystemObject)
    kAudioHardwarePropertyDefaultOutputDevice = _kAudioHardwarePropertyDefaultOutputDevice
    kAudioObjectPropertyScopeGlobal = _kAudioObjectPropertyScopeGlobal
    kAudioObjectPropertyElementMain = _kAudioObjectPropertyElementMain
    kAudioObjectPropertyName = _kAudioObjectPropertyName

    class AudioObjectPropertyAddress(ctypes.Structure):
        _fields_ = [
            ("mSelector", ctypes.c_uint32),
            ("mScope",    ctypes.c_uint32),
            ("mElement",  ctypes.c_uint32),
        ]

    # --- Step 1: get default output device ID ---
    addr = AudioObjectPropertyAddress(
        kAudioHardwarePropertyDefaultOutputDevice,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain,
    )
    device_id = ctypes.c_uint32(0)
    data_size = ctypes.c_uint32(ctypes.sizeof(device_id))

    ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
    ca.AudioObjectGetPropertyData.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(AudioObjectPropertyAddress),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    err = ca.AudioObjectGetPropertyData(
        kAudioObjectSystemObject,
        ctypes.byref(addr),
        0,
        None,
        ctypes.byref(data_size),
        ctypes.byref(device_id),
    )
    if err != 0:
        raise CoreAudioError(f"AudioObjectGetPropertyData (device id) returned {err:#010x}")

    # --- Step 2: get device name (CFStringRef) ---
    name_addr = AudioObjectPropertyAddress(
        kAudioObjectPropertyName,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain,
    )

    # We'll get a CFStringRef (pointer-sized)
    cfstr = ctypes.c_void_p(0)
    name_data_size = ctypes.c_uint32(ctypes.sizeof(cfstr))

    err = ca.AudioObjectGetPropertyData(
        device_id,
        ctypes.byref(name_addr),
        0,
        None,
        ctypes.byref(name_data_size),
        ctypes.byref(cfstr),
    )
    if err != 0:
        raise CoreAudioError(f"AudioObjectGetPropertyData (name) returned {err:#010x}")

    if not cfstr.value:
        raise CoreAudioError("Device name CFStringRef is NULL")

    # Convert CFStringRef → Python str via CoreFoundation
    # Release cfstr in a finally block so it is freed even on error,
    # but only if CoreFoundation loaded successfully.
    cf = None
    try:
        cf_path = ctypes.util.find_library("CoreFoundation")
        if not cf_path:
            raise CoreAudioError("CoreFoundation not found")
        cf = ctypes.cdll.LoadLibrary(cf_path)

        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        cf.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kCFStringEncodingUTF8 = 0x08000100
        max_len = cf.CFStringGetMaximumSizeForEncoding(cfstr, kCFStringEncodingUTF8) + 1

        buf = ctypes.create_string_buffer(max_len)
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32
        ]
        ok = cf.CFStringGetCString(cfstr, buf, max_len, kCFStringEncodingUTF8)
        if not ok:
            raise CoreAudioError("CFStringGetCString failed")

        return buf.value.decode("utf-8")

    except CoreAudioError:
        raise
    except Exception as exc:
        raise CoreAudioError(f"CFString conversion failed: {exc}") from exc
    finally:
        # Always release the CFStringRef if we have a CF handle and a valid ref.
        # Guard against double-release: zero cfstr.value after release.
        if cf is not None and cfstr.value:
            cf.CFRelease(cfstr)
            cfstr.value = None


def _get_default_output_name_via_system_profiler() -> str:
    """
    Fast path: query the default audio output device name via system_profiler.

    Runs: /usr/sbin/system_profiler SPAudioDataType -json -detailLevel mini
    Typical wall time: ~0.1–0.3 s (vs ~10–12 s for the CoreAudio ctypes path).

    JSON structure:
        { "SPAudioDataType": [ { "_items": [ {...}, ... ] } ] }
    Iterates _items; returns the _name of the first item where
        coreaudio_default_audio_output_device == "spaudio_yes".

    Raises CoreAudioError on:
      - missing system_profiler binary (FileNotFoundError)
      - timeout (SYSTEM_PROFILER_TIMEOUT exceeded)
      - non-zero exit code
      - invalid / unexpected JSON
      - no item marked as default output

    Does NOT log full system_profiler output.
    """
    try:
        result = subprocess.run(
            [
                SYSTEM_PROFILER_PATH,
                "SPAudioDataType",
                "-json",
                "-detailLevel", "mini",
            ],
            capture_output=True,
            timeout=SYSTEM_PROFILER_TIMEOUT,
        )
    except FileNotFoundError:
        raise CoreAudioError(
            f"system_profiler not found at {SYSTEM_PROFILER_PATH}"
        )
    except subprocess.TimeoutExpired:
        raise CoreAudioError(
            f"system_profiler timed out after {SYSTEM_PROFILER_TIMEOUT}s"
        )

    if result.returncode != 0:
        raise CoreAudioError(
            f"system_profiler exited {result.returncode}"
        )

    try:
        data = json.loads(result.stdout)
        top_items = data.get("SPAudioDataType", [])
        # Each top-level item has an _items list of device sub-records.
        for top in top_items:
            for dev in top.get("_items", []):
                if dev.get("coreaudio_default_audio_output_device") == "spaudio_yes":
                    name = dev.get("_name", "")
                    if name:
                        return name
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        raise CoreAudioError(f"system_profiler JSON parse error: {exc}") from exc

    raise CoreAudioError(
        "system_profiler: no device marked as default audio output"
    )


def get_default_output_device_name() -> str:
    """
    Return the display name of the macOS default output audio device.

    Primary path: /usr/sbin/system_profiler SPAudioDataType -json -detailLevel mini
    (typically ~0.1–0.3 s).  On any failure (missing binary, timeout, non-zero
    exit, invalid JSON, no default output item), falls back to the CoreAudio
    ctypes implementation in _get_default_output_device_name_coreaudio().

    Raises CoreAudioError only if both paths fail.
    """
    try:
        return _get_default_output_name_via_system_profiler()
    except CoreAudioError:
        pass  # fall through to CoreAudio ctypes
    return _get_default_output_device_name_coreaudio()


def is_airpods_device(name: str) -> bool:
    return "airpods" in name.lower()


# ---------------------------------------------------------------------------
# Keychain helper
# ---------------------------------------------------------------------------

class KeychainError(Exception):
    pass


def get_keychain_password(service: str, account: str) -> str:
    """
    Retrieve a password from the macOS Keychain via `security` CLI.
    The key is returned as a string, never logged.

    Raises KeychainError on failure.
    """
    cmd = [
        "/usr/bin/security",
        "find-generic-password",
        "-s", service,
        "-a", account,
        "-w",   # print password only
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise KeychainError("Keychain lookup timed out")
    except FileNotFoundError:
        raise KeychainError("`security` binary not found — not on macOS?")

    if result.returncode != 0:
        # Don't expose stderr (may contain partial key info)
        raise KeychainError(
            f"Keychain lookup failed (exit {result.returncode}). "
            "Check ELEVENLABS_KEYCHAIN_SERVICE and ELEVENLABS_KEYCHAIN_ACCOUNT."
        )
    key = result.stdout.strip()
    if not key:
        raise KeychainError("Keychain returned an empty password")
    return key


# ---------------------------------------------------------------------------
# ffplay helper
# ---------------------------------------------------------------------------

def find_ffplay() -> str:
    """Return path to ffplay, checking primary location then PATH."""
    if os.path.isfile(FFPLAY_PRIMARY) and os.access(FFPLAY_PRIMARY, os.X_OK):
        return FFPLAY_PRIMARY
    import shutil
    path = shutil.which("ffplay")
    if path:
        return path
    raise FileNotFoundError(
        f"ffplay not found at {FFPLAY_PRIMARY} and not on PATH. "
        "Install with: brew install ffmpeg"
    )


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def read_input(text_arg: Optional[str]) -> str:
    """
    If text_arg is '-', read from stdin. Otherwise use text_arg.
    Validates length and non-empty.
    """
    if text_arg == "-":
        text = sys.stdin.read()
    else:
        text = text_arg or ""

    text = text.strip()
    if not text:
        raise ValueError("Input text is empty")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(
            f"Input text is too long ({len(text)} chars, max {MAX_TEXT_CHARS})"
        )
    return text


# ---------------------------------------------------------------------------
# Debug timing helper
# ---------------------------------------------------------------------------

class _Timing:
    """
    Lightweight monotonic timer for stage-boundary profiling.
    Stage names and their timestamps are stored in insertion order.
    Never logs credentials or response bodies.

    Stage markers in chronological order:
      launch                  — main() entered, args parsed
      input-validated         — text/ID/timeout validation passed
      device-check            — first CoreAudio device query done
      request-start           — urlopen() about to be called
      headers-received        — urlopen() returned (HTTP headers received)
      first-audio-bytes-received — first read() call returned non-empty data
      ffplay-start            — subprocess.Popen for ffplay called
      first-bytes-to-ffplay   — first audio chunk written to ffplay stdin
      stream-eof              — HTTP body exhausted (read returned b"")
      ffplay-done             — ffplay process waited on / killed
    """

    # Canonical stage order for validation/summary sorting.
    STAGE_ORDER = [
        "launch",
        "input-validated",
        "device-check",
        "request-start",
        "headers-received",
        "first-audio-bytes-received",
        "ffplay-start",
        "first-bytes-to-ffplay",
        "stream-eof",
        "ffplay-done",
    ]

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled
        self._stages: list[tuple[str, float]] = []
        self._t0: float = time.monotonic()

    def mark(self, stage: str) -> None:
        """Record elapsed time at `stage` and print to stderr if debug enabled."""
        if not self._enabled:
            return
        elapsed = time.monotonic() - self._t0
        self._stages.append((stage, elapsed))
        print(f"[timing] {stage}: +{elapsed:.3f}s", file=sys.stderr)

    def stages(self) -> list[tuple[str, float]]:
        """Return a copy of recorded (stage, elapsed) pairs."""
        return list(self._stages)

    def stage_names(self) -> list[str]:
        """Return ordered list of recorded stage names."""
        return [name for name, _ in self._stages]

    def summary(self) -> str:
        """Return a one-line summary of all recorded stages."""
        parts = [f"{name}={elapsed:.3f}s" for name, elapsed in self._stages]
        return "timing: " + ", ".join(parts) if parts else "timing: (none)"


# ---------------------------------------------------------------------------
# Incremental stream read helper
# ---------------------------------------------------------------------------

def _read_chunk(resp: http.client.HTTPResponse, size: int) -> bytes:
    """
    Read up to `size` bytes from `resp` using resp.read1() when available,
    falling back to resp.read() otherwise.

    resp.read1() is defined on http.client.HTTPResponse (Python 3.x) and
    handles all HTTP framing correctly, including Transfer-Encoding: chunked
    decoding.  It returns whatever decoded body bytes are already buffered
    without blocking until `size` bytes arrive, giving lower first-audio
    latency than resp.read(size) while never exposing raw chunk-size lines
    or CRLF framing to the caller.

    Do NOT call resp.fp.read1() directly: fp is the raw socket BufferedReader
    and bypasses HTTPResponse's chunked-transfer-encoding decoder, which
    causes HTTP framing bytes (hex size lines, CRLF) to appear in the audio
    stream fed to ffplay, producing "Format mp3 detected only with low score"
    and "Header missing" errors.

    Returns b"" on EOF.  Raises OSError on network errors.
    """
    read1 = getattr(resp, "read1", None)
    if callable(read1):
        return read1(size)
    return resp.read(size)


# ---------------------------------------------------------------------------
# ElevenLabs streaming
# ---------------------------------------------------------------------------

class ElevenLabsError(Exception):
    pass


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", {}, fp)


def _tts_opener():
    """Private request boundary: never forward credentials on redirects."""
    return urllib.request.build_opener(_RejectRedirects())


def stream_tts(
    api_key: str,
    text: str,
    voice_id: str,
    model_id: str,
    output_format: str,
    timeout: int,
    debug: bool,
    stability: Optional[StabilityPreset] = None,
) -> http.client.HTTPResponse:
    """
    Open a streaming TTS request to ElevenLabs.
    Returns the open HTTPResponse for reading; caller must close it.

    Raises ElevenLabsError on non-200 or connectivity failure.
    """
    url = (
        ELEVENLABS_STREAM_URL.format(voice_id=urllib.parse.quote(voice_id, safe=""))
        + "?" + urllib.parse.urlencode({"output_format": output_format})
    )
    payload_data = {"text": text, "model_id": model_id}
    if stability is not None:
        payload_data["voice_settings"] = {
            "stability": stability.value,
        }
    payload = json.dumps(payload_data).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
    )

    if debug:
        print(f"[debug] POST {url}", file=sys.stderr)

    try:
        resp = _tts_opener().open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        exc.close()
        # Provider-controlled reasons, bodies, and destinations are private.
        raise ElevenLabsError(f"ElevenLabs API returned HTTP {exc.code}") from None
    except urllib.error.URLError:
        raise ElevenLabsError("ElevenLabs request failed") from None
    except (OSError, http.client.HTTPException):
        raise ElevenLabsError("ElevenLabs request failed") from None

    return resp


# ---------------------------------------------------------------------------
# Post-EOF playback drain helper
# ---------------------------------------------------------------------------

def _drain_ffplay(
    proc: "subprocess.Popen",
    device_changed: "threading.Event",
    bytes_written: int,
    debug: bool,
    _t: "_Timing",
) -> int:
    """
    Wait for ffplay to drain its audio buffer after stdin EOF.

    Called after proc.stdin is already closed; the player still has buffered
    audio to decode and play.  Polls every _DRAIN_POLL_SECS seconds checking
    (in fail-closed priority order):

      1. device_changed set  → terminate ffplay, return 3.
      2. proc.poll() != None → success only for exit 0, otherwise failure 1.
      3. Safety deadline hit → kill hung player; return 1 (not success).

    Safety timeout is duration-aware:
        max(_DRAIN_TIMEOUT_MIN_SECS,
            min(bytes_written / _MP3_128K_BYTES_PER_SEC * _DRAIN_TIMEOUT_SCALE,
                _DRAIN_TIMEOUT_MAX_SECS))

    Returns:
        0  playback completed naturally
        1  safety timeout exceeded (ffplay killed — not success)
        3  AirPods changed/disconnected during drain
    """
    if bytes_written > 0:
        estimated_secs = bytes_written / _MP3_128K_BYTES_PER_SEC
    else:
        estimated_secs = 0.0

    safety_secs = max(
        _DRAIN_TIMEOUT_MIN_SECS,           # floor: OS buffer jitter + decoder startup
        min(estimated_secs * _DRAIN_TIMEOUT_SCALE, _DRAIN_TIMEOUT_MAX_SECS),
    )

    if debug:
        print(
            f"[debug] drain: bytes_written={bytes_written}, "
            f"estimated_audio={estimated_secs:.1f}s, "
            f"safety_timeout={safety_secs:.1f}s",
            file=sys.stderr,
        )

    deadline = time.monotonic() + safety_secs

    while True:
        # Device change: fail-closed, always checked first.
        if device_changed.is_set():
            print(
                "AirPods output changed or disconnected mid-playback. Stopping.",
                file=sys.stderr,
            )
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            return 3

        status = proc.poll()
        if status is not None:
            if device_changed.is_set():
                return 3
            if status == 0:
                return 0
            print("Playback error: ffplay failed during drain.", file=sys.stderr)
            return 1

        # Safety deadline: kill hung player, report non-success.
        if time.monotonic() >= deadline:
            print(
                f"Playback drain timeout: ffplay did not finish within "
                f"{safety_secs:.0f}s safety limit. Stopping.",
                file=sys.stderr,
            )
            proc.kill()
            return 1

        time.sleep(_DRAIN_POLL_SECS)


# ---------------------------------------------------------------------------
# Playback with mid-stream device monitoring
# ---------------------------------------------------------------------------

class _StreamReader:
    """One HTTPResponse owner; bounded buffering and cancellable blocking reads."""

    def __init__(self, resp: http.client.HTTPResponse) -> None:
        self.resp = resp
        self.chunks = queue.Queue(maxsize=8)
        self.cancelled = threading.Event()
        self.done = threading.Event()
        self.failed = False
        # Capture before read1 can set fp=None. shutdown wakes a socket read
        # without acquiring BufferedReader's lock; only the worker closes resp.
        raw = getattr(getattr(resp, "fp", None), "raw", None)
        self.socket = getattr(raw, "_sock", None)
        self.thread = threading.Thread(
            target=self._read, name="airpods-stream-reader", daemon=True,
        )

    def _read(self) -> None:
        try:
            while not self.cancelled.is_set():
                chunk = _read_chunk(self.resp, STREAM_CHUNK)
                if not chunk:
                    remaining = getattr(self.resp, "length", None)
                    if isinstance(remaining, int) and remaining > 0:
                        raise http.client.HTTPException("incomplete response")
                    break
                while not self.cancelled.is_set():
                    try:
                        self.chunks.put(chunk, timeout=0.05)
                        break
                    except queue.Full:
                        continue
        except Exception:
            # Never let worker failures disappear into a thread traceback or
            # turn into clean EOF; the consumer emits a fixed diagnostic.
            self.failed = True
        finally:
            try:
                self.resp.close()
            except Exception:
                self.failed = True
            finally:
                # EOF/errors cannot get stranded behind a full queue sentinel.
                self.done.set()

    def stop(self) -> None:
        self.cancelled.set()
        if self.socket is not None:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.thread.ident is not None:
            self.thread.join(timeout=1)
        else:
            self.resp.close()


def _stop_ffplay(proc) -> None:
    """Terminate/kill and reap with finite waits, including failed drains."""
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def play_stream(
    resp: http.client.HTTPResponse,
    ffplay_path: str,
    expected_device_name: str,
    debug: bool,
    poll_interval: float = 2.0,
    timing: Optional["_Timing"] = None,
    timeout: float = 30,
) -> int:
    """
    Pipe the HTTP response body into ffplay, owning response closure.
    Monitors the default output device every `poll_interval` seconds;
    terminates ffplay immediately if the device changes.

    A blocking HTTPResponse.read1() worker decodes framing and forwards audio
    through a bounded queue. The consumer enforces body-data inactivity while
    monitoring device/player state. Queue pressure is not a network timeout.

    After the HTTP stream reaches EOF, stdin is closed and _drain_ffplay() is
    called to let ffplay play out its buffer naturally.  _drain_ffplay() uses
    a duration-aware safety timeout so a hung player cannot persist forever,
    and continues monitoring the AirPods device throughout the drain phase.

    Returns an exit code:
        0  - playback completed (stream + drain both succeeded)
        1  - player failure or drain safety timeout
        3  - device changed/disconnected mid-playback or during drain
        5  - body-network failure or inactivity timeout
    """
    ffplay_cmd = [
        ffplay_path,
        "-nodisp",           # no video window
        "-autoexit",         # exit when done
        "-loglevel", "quiet" if not debug else "warning",
        "-i", "pipe:0",      # read from stdin
    ]

    if debug:
        print(f"[debug] ffplay cmd: {ffplay_cmd}", file=sys.stderr)

    # Use caller-supplied timing context or create a no-op one.
    _t = timing if timing is not None else _Timing(enabled=debug)

    try:
        proc = subprocess.Popen(
            ffplay_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=None if debug else subprocess.DEVNULL,
            bufsize=0,
        )
    except BaseException:
        resp.close()
        raise

    reader = _StreamReader(resp)
    _t.mark("ffplay-start")

    # Signal handling: forward SIGINT/SIGTERM to ffplay.
    # Save prior handlers so they can be restored in the finally block.
    def _handle_signal(signum, frame):
        if proc.poll() is None:
            proc.terminate()
        sys.exit(1)

    prior_sigint  = signal.signal(signal.SIGINT,  _handle_signal)
    prior_sigterm = signal.signal(signal.SIGTERM, _handle_signal)

    # Device monitor in a background thread.
    # Performs an immediate check first, then waits between subsequent polls.
    device_changed = threading.Event()
    monitor_stop = threading.Event()

    def _monitor_device():
        # Immediate check before the first sleep so a pre-existing change is
        # caught without waiting a full poll_interval.
        while not monitor_stop.is_set():
            try:
                current = get_default_output_device_name()
            except CoreAudioError:
                # Can't read device — fail closed.
                device_changed.set()
                break
            if not is_airpods_device(current) or current != expected_device_name:
                device_changed.set()
                break
            # Wait before next poll; exit early if main loop signals done.
            monitor_stop.wait(poll_interval)

    monitor_thread = threading.Thread(target=_monitor_device, daemon=True)

    first_audio_received = False
    first_bytes_written = False
    bytes_written = 0    # track bytes piped for duration-aware drain timeout
    exit_code = 0

    # Idempotent stdin closer — used in the drain path and again in finally.
    _stdin_closed = False

    def _close_stdin_safe() -> None:
        nonlocal _stdin_closed
        if not _stdin_closed:
            _stdin_closed = True
            try:
                proc.stdin.close()
            except OSError:
                pass

    try:
        # Unbuffered, nonblocking stdin keeps a stalled player from preventing
        # device checks, cancellation, or a bounded close of the player's pipe.
        os.set_blocking(proc.stdin.fileno(), False)
        reader.thread.start()
        monitor_thread.start()
        last_audio = time.monotonic()
        while True:
            # Check device change
            if device_changed.is_set():
                print(
                    "AirPods output changed or disconnected mid-playback. Stopping.",
                    file=sys.stderr,
                )
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                exit_code = 3
                break

            # Check if ffplay died unexpectedly (before stream EOF).
            # Device change takes priority: if a concurrent device switch
            # caused ffplay to die, report exit 3 rather than 1.
            if proc.poll() is not None:
                if device_changed.is_set():
                    print(
                        "AirPods output changed or disconnected mid-playback. Stopping.",
                        file=sys.stderr,
                    )
                    exit_code = 3
                else:
                    print(
                        "Playback error: ffplay exited unexpectedly before stream EOF.",
                        file=sys.stderr,
                    )
                    exit_code = 1
                break

            try:
                chunk = reader.chunks.get(timeout=0.05)
                last_audio = time.monotonic()
            except queue.Empty:
                if device_changed.is_set():
                    exit_code = 3
                    break
                if reader.done.is_set():
                    # A producer can enqueue its last bytes between get's
                    # timeout and this done check. Once done, no more puts can
                    # occur: check the queue again before declaring EOF/error.
                    try:
                        chunk = reader.chunks.get_nowait()
                        last_audio = time.monotonic()
                    except queue.Empty:
                        if reader.failed:
                            print("ElevenLabs stream read failed.", file=sys.stderr)
                            exit_code = 5
                            break
                        chunk = b""
                elif time.monotonic() - last_audio >= timeout:
                    print("ElevenLabs stream inactivity timeout.", file=sys.stderr)
                    exit_code = 5
                    break
                else:
                    continue

            if not chunk:
                _t.mark("stream-eof")
                # Device check at EOF before entering drain phase.
                if device_changed.is_set():
                    print(
                        "AirPods output changed or disconnected mid-playback. Stopping.",
                        file=sys.stderr,
                    )
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    exit_code = 3
                    break
                # Close stdin (signals EOF to ffplay) then drain buffered audio.
                # _drain_ffplay() monitors the device throughout the drain phase.
                _close_stdin_safe()
                exit_code = _drain_ffplay(
                    proc, device_changed, bytes_written, debug, _t
                )
                break

            # Mark first audio bytes received from the network
            if not first_audio_received:
                _t.mark("first-audio-bytes-received")
                first_audio_received = True

            # Write to ffplay stdin
            try:
                if not first_bytes_written:
                    _t.mark("first-bytes-to-ffplay")
                    first_bytes_written = True
                offset = 0
                while offset < len(chunk):
                    if device_changed.is_set():
                        exit_code = 3
                        break
                    if proc.poll() is not None:
                        raise BrokenPipeError()
                    written = proc.stdin.write(chunk[offset:])
                    if written:
                        offset += written
                        bytes_written += written
                    else:
                        monitor_stop.wait(0.05)
                proc.stdin.flush()
                # Time spent delivering queued audio is backpressure, not a
                # period of waiting for server data.
                last_audio = time.monotonic()
                if exit_code:
                    break
            except OSError:
                # Device change takes priority: if the device change caused
                # ffplay to be killed (breaking the pipe), report exit 3.
                if device_changed.is_set():
                    print(
                        "AirPods output changed or disconnected mid-playback. Stopping.",
                        file=sys.stderr,
                    )
                    exit_code = 3
                else:
                    print(
                        "Playback error: ffplay stdin closed unexpectedly.",
                        file=sys.stderr,
                    )
                    exit_code = 1
                break

    finally:
        if device_changed.is_set():
            exit_code = 3
        monitor_stop.set()
        reader.stop()
        if monitor_thread.ident is not None:
            monitor_thread.join(timeout=3)

        # Restore original signal handlers.
        signal.signal(signal.SIGINT,  prior_sigint)
        signal.signal(signal.SIGTERM, prior_sigterm)

        _stop_ffplay(proc)
        _close_stdin_safe()

        _t.mark("ffplay-done")
        if debug:
            print(f"[timing] {_t.summary()}", file=sys.stderr)

    return exit_code


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="airpods_say",
        description=(
            "Stream ElevenLabs TTS through the Mac's current AirPods output."
        ),
    )
    p.add_argument(
        "text",
        nargs="?",
        default=None,
        help='Text to speak, or "-" to read from stdin.',
    )
    p.add_argument(
        "--voice-id",
        default=os.environ.get("ELEVENLABS_VOICE_ID"),
        help="ElevenLabs voice ID (or set ELEVENLABS_VOICE_ID; no default).",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL_ID,
        help=f"ElevenLabs model ID (default: {DEFAULT_MODEL_ID})",
    )
    p.add_argument(
        "--stability",
        choices=tuple(STABILITY_PRESETS),
        help=(
            "ElevenLabs v3 stability preset (default: natural for v3)."
        ),
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=30,
        help=("Request/header and body-data inactivity timeout in seconds, "
              "must be > 0 (default: 30)"),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Check dependencies, device, and input without accessing "
            "Keychain, network, or playing audio."
        ),
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug output.",
    )
    return p


def validate_ids(voice_id: str, model_id: str) -> None:
    if not _VOICE_ID_RE.match(voice_id):
        raise ValueError(
            f"Invalid voice ID (must match {_VOICE_ID_RE.pattern!r})."
        )
    validate_model_id(model_id)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Stage timer starts immediately so wall-clock cost of each phase is visible
    # in --debug output.  Stages (in order): launch → input-validated →
    # device-check → request-start → headers-received →
    # first-audio-bytes-received → ffplay-start → first-bytes-to-ffplay →
    # stream-eof → ffplay-done.
    t = _Timing(enabled=args.debug)
    t.mark("launch")

    # --- Validate IDs ---
    try:
        if not args.voice_id or not args.voice_id.strip():
            raise ValueError("Set --voice-id or ELEVENLABS_VOICE_ID.")
        validate_ids(args.voice_id, args.model)
        stability = resolve_stability(args.model, args.stability)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # --- Validate timeout ---
    if args.timeout <= 0:
        print(
            f"Error: --timeout must be a positive integer, got {args.timeout}",
            file=sys.stderr,
        )
        return 1

    # --- Validate / read input ---
    try:
        text = read_input(args.text)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    t.mark("input-validated")

    if args.debug:
        print(f"[debug] text length: {len(text)} chars", file=sys.stderr)

    # --- Check ffplay ---
    try:
        ffplay_path = find_ffplay()
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 6

    if args.debug:
        print(f"[debug] ffplay: {ffplay_path}", file=sys.stderr)

    # --- Check output device (first time) ---
    try:
        device_name = get_default_output_device_name()
    except CoreAudioError as exc:
        print(f"Error detecting audio output device: {exc}", file=sys.stderr)
        return 2

    t.mark("device-check")

    if args.debug:
        print(f"[debug] default output: {device_name!r}", file=sys.stderr)

    if not is_airpods_device(device_name):
        print(
            f"Error: Current default output is '{device_name}', not AirPods. "
            "Switch to AirPods and try again.",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        stability_text = (
            f"{stability.name} ({stability.value})"
            if stability is not None
            else "legacy (unset)"
        )
        print(
            f"[dry-run] OK — device={device_name!r}, text={len(text)} chars, "
            f"voice={args.voice_id!r}, model={args.model!r}, "
            f"stability={stability_text}, "
            f"ffplay={ffplay_path}",
            file=sys.stderr,
        )
        return 0

    # Select an existing Keychain item only after the active-output gate.
    service = os.environ.get("ELEVENLABS_KEYCHAIN_SERVICE")
    account = os.environ.get("ELEVENLABS_KEYCHAIN_ACCOUNT")
    for name, value in (
        ("ELEVENLABS_KEYCHAIN_SERVICE", service),
        ("ELEVENLABS_KEYCHAIN_ACCOUNT", account),
    ):
        if not value or not value.strip():
            print(f"Error: Set {name} to a non-empty value.", file=sys.stderr)
            return 1

    # --- Get API key from Keychain ---
    try:
        api_key = get_keychain_password(service, account)
    except KeychainError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 4

    # --- Re-check device immediately before playback ---
    try:
        device_name_2 = get_default_output_device_name()
    except CoreAudioError as exc:
        print(f"Error re-checking audio output device: {exc}", file=sys.stderr)
        return 2

    if not is_airpods_device(device_name_2) or device_name_2 != device_name:
        print(
            f"Error: AirPods output changed before playback started "
            f"(was '{device_name}', now '{device_name_2}'). Aborting.",
            file=sys.stderr,
        )
        return 2

    # --- Stream TTS ---
    t.mark("request-start")
    try:
        resp = stream_tts(
            api_key=api_key,
            text=text,
            voice_id=args.voice_id,
            model_id=args.model,
            output_format=DEFAULT_OUTPUT_FORMAT,
            timeout=args.timeout,
            debug=args.debug,
            stability=stability,
        )
    except ElevenLabsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 5
    finally:
        # Ensure API key is not retained longer than needed
        del api_key

    t.mark("headers-received")

    # Playback owns response closure; never close it concurrently with a read.
    return play_stream(
        resp=resp,
        ffplay_path=ffplay_path,
        expected_device_name=device_name_2,
        debug=args.debug,
        timing=t,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
