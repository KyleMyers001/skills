---
name: elevenlabs-voice-policy
description: >-
  Internal model and stability policy used by airpods-say. Load when maintaining
  that integration, not when selecting a user-facing speech command.
---

# ElevenLabs Voice Policy

This dependency supplies the model default, stability presets, and model-ID
validation in `scripts/elevenlabs_voice.py`. It contains no voice identifier or
credential defaults. The consuming skill obtains those settings from the user.

Deploy this directory as a sibling of `airpods-say`. Do not invoke this skill as
a speech command. The current default model is `eleven_v4`; named stability
presets apply only when `eleven_v3` is explicitly selected.
