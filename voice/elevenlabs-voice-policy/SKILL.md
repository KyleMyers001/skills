---
name: elevenlabs-voice-policy
description: >-
  Internal TTS request and voice policy used by airpods-say and sonos-say. Load
  when maintaining either integration, not when selecting a speech command.
---

# ElevenLabs Voice Policy

This dependency supplies model/stability policy, strict voice/model validation,
exact-text payload construction, bounded sanitized Keychain lookup, a private
redirect-rejecting opener, and `audio/mpeg` header validation in
`scripts/elevenlabs_voice.py`. It contains no voice identifier or credential
defaults. IDs are type-checked and full-matched without stripping whitespace.
Both `airpods-say` and `sonos-say` obtain user settings
outside this dependency. Consumers retain stream/file reading, playback,
timeout selection, device/topology logic, and their own error adapters. Shared
rules must not collapse AirPods exits 4/5 into Sonos's phase markers.

For copied installations, deploy this directory as a sibling of each consumer.
Whole-consumer symlinks resolve this dependency beside their real checkout;
no separate policy symlink is needed. Do not invoke this skill as a speech
command. The current default model is `eleven_v4`; named stability
presets apply only when `eleven_v3` is explicitly selected.
