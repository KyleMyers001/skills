"""Shared ElevenLabs identifiers, payload policy, and private request boundaries."""

import http.client
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple, Optional


DEFAULT_MODEL_ID = "eleven_v4"
DEFAULT_STABILITY = "natural"
_MODEL_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_VOICE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# Only stability is sent intentionally; other voice settings use server
# defaults.
STABILITY_PRESETS = {
    "creative": 0.0,
    "natural": 0.5,
    "robust": 1.0,
}


class StabilityPreset(NamedTuple):
    """A validated ElevenLabs v3 stability selection."""

    name: str
    value: float


def validate_model_id(model_id: str) -> str:
    """Validate and return an ElevenLabs model identifier."""
    if not isinstance(model_id, str) or not _MODEL_ID_RE.fullmatch(model_id):
        raise ValueError(
            f"Invalid model ID (must match {_MODEL_ID_RE.pattern!r}): "
            f"{model_id!r}"
        )
    return model_id


def validate_voice_id(voice_id: str) -> str:
    if not isinstance(voice_id, str) or not _VOICE_ID_RE.fullmatch(voice_id):
        raise ValueError("Invalid voice ID (letters, digits, underscores, hyphens; 1–64 characters).")
    return voice_id


class KeychainLookupError(RuntimeError):
    """Locally authored credential diagnostic, never selectors or stderr."""


def keychain_password(service: str, account: str) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service,
             "-a", account, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise KeychainLookupError("Keychain lookup timed out") from None
    except (OSError, subprocess.CalledProcessError):
        raise KeychainLookupError("Keychain lookup failed") from None
    if result.returncode != 0:
        raise KeychainLookupError("Keychain lookup failed")
    key = result.stdout.strip()
    if not key:
        raise KeychainLookupError("Keychain returned an empty password")
    return key


class TTSRequestError(RuntimeError):
    def __init__(self, status=None):
        super().__init__("ElevenLabs request failed")
        self.status = status


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", {}, fp)


def tts_opener():
    """Private opener: never forward credentials on redirects."""
    return urllib.request.build_opener(_RejectRedirects())


def tts_payload(text: str, model_id: str, stability: Optional[StabilityPreset]):
    validate_model_id(model_id)
    data = {"text": text, "model_id": model_id}
    if stability is not None:
        data["voice_settings"] = {"stability": stability.value}
    return data


def validate_audio_mime(mime) -> None:
    if (not isinstance(mime, str)
            or mime.split(";", 1)[0].strip().lower() != "audio/mpeg"):
        raise TTSRequestError()


def open_tts(endpoint, api_key, text, model_id, output_format, timeout, stability=None):
    """Open validated audio headers; consumers own body reads and closure."""
    url = endpoint + "?" + urllib.parse.urlencode({"output_format": output_format})
    req = urllib.request.Request(
        url, data=json.dumps(tts_payload(text, model_id, stability)).encode("utf-8"),
        method="POST", headers={"xi-api-key": api_key,
                                "Content-Type": "application/json", "Accept": "audio/mpeg"},
    )
    try:
        resp = tts_opener().open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise TTSRequestError(exc.code) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        raise TTSRequestError() from None
    try:
        if resp.status != 200:
            raise TTSRequestError(resp.status)
        validate_audio_mime(resp.headers.get("Content-Type"))
    except TTSRequestError:
        resp.close()
        raise
    except Exception:
        resp.close()
        raise TTSRequestError() from None
    except BaseException:
        resp.close()
        raise
    return resp


def resolve_stability(
    model_id: str,
    stability: Optional[str],
) -> Optional[StabilityPreset]:
    """Resolve v3's default preset while preserving legacy model payloads."""
    if stability is not None and stability not in STABILITY_PRESETS:
        choices = ", ".join(sorted(STABILITY_PRESETS))
        raise ValueError(
            f"Unknown stability preset {stability!r}; choose from {choices}."
        )
    if model_id != "eleven_v3":
        if stability is not None:
            raise ValueError(
                "--stability is only supported with eleven_v3; omit it "
                "when using another model."
            )
        return None

    name = stability or DEFAULT_STABILITY
    return StabilityPreset(name, STABILITY_PRESETS[name])
