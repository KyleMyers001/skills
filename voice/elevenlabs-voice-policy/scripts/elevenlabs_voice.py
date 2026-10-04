"""Shared ElevenLabs model defaults and stability policy."""

import re
from typing import NamedTuple, Optional


DEFAULT_MODEL_ID = "eleven_v4"
DEFAULT_STABILITY = "natural"
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

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
    if not _MODEL_ID_RE.match(model_id):
        raise ValueError(
            f"Invalid model ID (must match {_MODEL_ID_RE.pattern!r}): "
            f"{model_id!r}"
        )
    return model_id


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
