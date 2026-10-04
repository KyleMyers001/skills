---
name: elevenlabs-voice-policy
description: >-
  Internal model and stability policy used by airpods-say and sonos-say. Load
  when maintaining either integration, not when selecting a speech command.
---

# ElevenLabs Voice Policy

This dependency supplies the model default, stability presets, and model-ID
validation in `scripts/elevenlabs_voice.py`. It contains no voice identifier or
credential defaults. Both `airpods-say` and `sonos-say` obtain user settings
outside this dependency.

For copied installations, deploy this directory as a sibling of each consumer.
Whole-consumer symlinks resolve this dependency beside their real checkout;
no separate policy symlink is needed. Do not invoke this skill as a speech
command. The current default model is `eleven_v4`; named stability
presets apply only when `eleven_v3` is explicitly selected.
