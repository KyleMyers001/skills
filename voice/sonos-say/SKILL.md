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
- A reachable, supported Sonos speaker on a trusted LAN. AVTransport event
  callbacks require inbound LAN connectivity; only SoCo's default threaded
  event backend is supported. Do not change firewall settings automatically.
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
positive when explicitly supplied. Otherwise playback uses
`ceil(MP3 bytes / 16000) + 30` seconds, derived after synthesis.
`--tts-timeout` independently limits synthesis/header/body socket waits
(default: 180 seconds; finite and positive). These are not total wall-clock
limits across blocking SoCo calls. `--help` requires no configuration.

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

- A true multi-room coordinator is refused: room-targeted speech must not
  broadcast into other rooms. An ungrouped home-theater primary and its explicitly
  owned satellites count as one room; playback addresses only the primary without
  unjoining or changing its bonds. An ordinary grouped non-coordinator is isolated,
  then rejoined to its exact prior group. Bonded rooms grouped with another room,
  direct satellite/hidden targets, stereo pairs, and unknown topology are refused.
- Read-only discovery and fresh topology validation precede Keychain lookup
  (bounded to 10 seconds) and synthesis. Synthesis finishes before any group
  mutation. Independent safety observations invalidate SoCo's polling cache;
  active topology subscriptions are unsupported and fail closed. Device roles
  must come from the same processed observation. Exact room membership and
  satellite ownership are rechecked before snapshot and playback; concurrent
  controllers remain a risk.
- Unsupported cloud queue sources (`x-rincon-queue:…#<nonzero>`) are refused
  before source replacement; SoCo cannot reliably restart them. Refusal after
  isolation performs regroup-only cleanup. Transport, mute, and volume remain
  unchanged when the source is refused.
- TTS redirects and non-MP3 MIME/empty bodies are refused. Never expose keys or
  provider error bodies, and never put the API key in arguments or workspace files.
- Temporary audio uses a fresh random URL restricted to the target speaker's IP.
  Never share or log that URL. HTTP remains unencrypted; this is not safe on an
  untrusted LAN. Temporary audio and the server are cleaned up after use.
- Playback must reach PLAYING and then STOPPED within the deadline. An
  attempt-scoped AVTransport recorder subscribes before Play, retaining short
  clips missed by polling. Prior/unrelated/stale events, lost event evidence,
  pausing, non-start, and timeout cannot prove success.
- After snapshot, the target is temporarily unmuted for delivery. Original mute
  and volume must be restored and read back; mismatches are restoration failures.
  Every target is rechecked for exact isolation before transport restoration and
  afterward, including detached ordinary members. Unavailable topology reads
  retry boundedly; observed unsafe topology is never retried into acceptance.
  Unsafe topology prevents transport/group writes, but target-local mute/volume
  fallback requires fresh device-description identity, never SoCo's cached UID.
  No automatic bond repair or unrelated regrouping is made. Refusal before the
  first state-changing RPC incurs no restoration writes; an attempted unjoin
  still requires regroup-only recovery.
- Scoped SIGINT/SIGTERM handling covers live preflight through temporary-file
  deletion. Cleanup attempts captured transport, URI, volume, and group
  restoration; group recovery is armed only at a validated unjoin RPC attempt.
  Restoration failure takes precedence over playback errors or interrupts.
  State restoration is not frame-accurate.

Bonded-room playback and TV/HDMI restoration, idle empty-URI clearing, and real
short-clip event delivery remain hardware-gated. Offline tests do not establish
firmware/firewall acceptance;
hold merge readiness until those checks are separately approved and verified,
or the supported scope is explicitly restricted. Detached members skip the
empty-URI clear because exact regrouping replaces the temporary source.

On exit 0, briefly report helper-observed completion and successful cleanup.
On failure, report only the fixed `SONOS_SAY_ERROR` code.
`SONOS_RESTORE_FAILED` means the room
may need manual restoration/regrouping even if the announcement was heard. Do not
substitute a macOS system voice unless the user explicitly requests a fallback.

Read [technical notes](references/NOTES.md) when troubleshooting lifecycle,
security boundaries, or exit codes.
