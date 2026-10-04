---
name: sonos-say
description: >-
  Speak short announcements through a requested Sonos room using ElevenLabs.
  Use only when the user explicitly requests Sonos speech or playback; mentioning
  this skill during maintenance does not authorize an announcement.
---

# Sonos Say

## Prerequisites

- Python 3 and SoCo (`python3 -m pip install soco` if installation is approved).
- For copied installations, deploy `elevenlabs-voice-policy` alongside this
  skill. A whole-skill symlink resolves shared policy in its real checkout;
  no separate policy symlink is needed.
- A reachable, supported Sonos speaker on a trusted LAN.
- Set `ELEVENLABS_VOICE_ID` to the user's own voice ID and `SONOS_ROOM` to their
  intended room. `--voice-id` and `--room` override these values; neither has a
  bundled default.
- Normal synthesis requires `ELEVENLABS_KEYCHAIN_SERVICE` and
  `ELEVENLABS_KEYCHAIN_ACCOUNT`, selecting an existing macOS Keychain item.
  These settings contain selectors, not the API key. Do not inspect, create,
  modify, or rotate credentials as part of ordinary playback.

Speech text is sent to ElevenLabs. Do not synthesize sensitive text without the
user's authorization. Keep announcements short; the hard limit is 5,000 characters.

## Run

Resolve this skill's installed directory; do not assume placeholder substitution.
From that directory, validate setup without credentials, network, or playback:

```sh
python3 scripts/sonos_say.py --dry-run --room 'Test Room' \
  --voice-id YOUR_VOICE_ID 'Test announcement'
```

For real speech, use the configured room or the room explicitly requested by the
user. Pass literal, simple text as a quoted argument:

```sh
python3 scripts/sonos_say.py 'Dinner is ready.'
```

For complex or multiline text, pass it on standard input without shell
interpolation:

```sh
python3 scripts/sonos_say.py -
```

Default volume is `35`. Change `--volume` only when requested or clearly necessary;
volume effects depend on the room and speaker. `--timeout` must be finite and
positive (default: 120 seconds). `--help` requires no configuration.

## Voice direction

- The shared policy defaults to `eleven_v4`; do not describe v3 as the default.
  This is the configured model, not a guarantee of provider availability.
- To use stability presets, explicitly select `--model eleven_v3`.
  Natural (`0.5`) is its default; Creative (`0.0`) and Robust (`1.0`) are optional.
- For other models, omit `--stability`; no stability settings are sent.
- Expression/audio tags remain exact authored speech. Never infer, insert,
  rewrite, or strip them. Use supported tags sparingly when authoring speech,
  not automatic theatrical tags for ordinary announcements.

## Safety and results

- A grouped coordinator is refused: room-targeted speech must not broadcast to
  its group. A grouped non-coordinator is isolated, then rejoined to its exact
  prior group. Bonded, hidden, malformed, or unavailable topology is refused.
- Read-only discovery and fresh topology validation precede Keychain lookup
  (bounded to 10 seconds) and synthesis. Synthesis finishes before any group
  mutation. Independent safety observations invalidate SoCo's polling cache;
  active topology subscriptions are unsupported and fail closed. Isolation is
  rechecked before playback; concurrent controllers remain a risk.
- TTS redirects are refused before a second request. Never expose keys or
  provider error bodies, and never put the API key in arguments or workspace files.
- Temporary audio uses a fresh random URL restricted to the target speaker's IP.
  Never share or log that URL. HTTP remains unencrypted; this is not safe on an
  untrusted LAN. Temporary audio and the server are cleaned up after use.
- Playback must reach PLAYING and then STOPPED within the deadline. Missing
  telemetry, pausing, non-start, and timeout are failures, not success.
- Scoped SIGINT/SIGTERM handling covers live preflight through temporary-file
  deletion. Cleanup attempts captured transport, URI, volume, and group
  restoration; group recovery is armed only at a validated unjoin RPC attempt.
  Restoration failure takes precedence over playback errors or interrupts.
  State restoration is not frame-accurate.

On exit 0, briefly report playback completion and successful cleanup. On failure,
report only the fixed `SONOS_SAY_ERROR` code. `SONOS_RESTORE_FAILED` means the room
may need manual restoration/regrouping even if the announcement was heard. Do not
substitute a macOS system voice unless the user explicitly requests a fallback.

Read [technical notes](references/NOTES.md) when troubleshooting lifecycle,
security boundaries, or exit codes.
