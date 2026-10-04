# Sonos Say — Technical Notes

## Dependencies and configuration

The implementation uses Python's standard library, SoCo, and the sibling
`elevenlabs-voice-policy/scripts/elevenlabs_voice.py`. Package both skills at the
same directory level. The shared policy owns `DEFAULT_MODEL_ID` (`eleven_v4`)
and v3 stability presets, not user voice IDs or credential selectors.

- `--voice-id` overrides `ELEVENLABS_VOICE_ID`; no bundled voice.
- `--room` overrides `SONOS_ROOM`; no implicit destination.
- Normal execution requires `ELEVENLABS_KEYCHAIN_SERVICE` and
  `ELEVENLABS_KEYCHAIN_ACCOUNT`. They select an existing Keychain item.
  The key is retrieved through `security find-generic-password`, never logged.
- Help needs no configuration. Dry-run needs room/voice but no Keychain selectors.
  It validates and describes the request without network, credentials, discovery,
  or speaker access. Its output includes the submitted text, so treat it accordingly.
- Text, identifiers, volume, timeout, and model/stability combinations are validated
  before sensitive I/O. Timeout must be finite and positive, including direct calls.

For approved setup, configure selectors for an existing user-owned Keychain item:

```sh
export ELEVENLABS_KEYCHAIN_SERVICE='your-tts-service'
export ELEVENLABS_KEYCHAIN_ACCOUNT='your-tts-account'
export ELEVENLABS_VOICE_ID='YOUR_VOICE_ID'
export SONOS_ROOM='Your Room'
```

Do not put the API key into these variables, command-line arguments, or a workspace
file. Credential creation or rotation is a separate, user-authorized operation.

## Pipeline

1. Validate configuration and read the selected Keychain item.
2. Send the exact speech text to ElevenLabs and write a temporary MP3.
3. Discover the room by case-insensitive name; reject missing/ambiguous matches.
4. Inspect topology and reject unsupported announcement targets.
5. Install cleanup-aware signal handling; isolate a grouped non-coordinator.
6. Capture transport state and original volume.
7. Start the restricted HTTP server, recheck isolation, set volume, and play.
8. Wait for PLAYING followed by STOPPED within the configured deadline.
9. Shut down the server; restore captured state, volume, and original grouping.
10. Remove temporary files and report the outcome after cleanup.

Synthesis completes before group changes. SoCo uses multicast UPnP discovery;
there are no fixed speaker IPs. The local bind address is selected by routing
with the target speaker's IP, not by binding to all interfaces.

## Voice policy

The v4 default sends only text and model ID. An explicit `eleven_v3` selection
adds `voice_settings.stability`: Creative (`0.0`), Natural (`0.5`, implicit for
v3), or Robust (`1.0`). Explicit stability with a non-v3 model is rejected before
I/O. No other voice settings are added. Tags are passed through unchanged.

From the installed skill directory:

```sh
python3 scripts/sonos_say.py --model eleven_v3 --stability robust 'A reminder.'
```

Configured model names do not establish current API availability or live
provider compatibility; offline tests validate request construction only.

## Network security

A private urllib opener refuses redirects before another request, including
same-origin redirects. Non-success responses are not saved as audio. Errors
exclude provider-controlled bodies, reasons, redirect destinations, and keys.

Each HTTP server serves exactly one path containing a new 256-bit random token.
GET and HEAD require both the capability path and the target speaker's source IP;
all unauthorized requests receive 404 without reading audio or file metadata.
Query strings do not bypass the path check. Successful responses specify
`Cache-Control: no-store`. Access logs and application logs omit the capability.

The server uses a random port on the selected LAN interface. Daemon request
threads, a 10-second connection timeout, a 0.5-second shutdown poll interval,
and socket closure keep shutdown bounded. Broken pipes and connection resets
are benign disconnects; other handler errors receive fixed diagnostics.

This is unencrypted HTTP, not strong client authentication. An on-path observer
can still capture audio or the token. Use a trusted LAN; there is no permissive
fallback for clients whose IP differs. Live speaker/firmware compatibility has
not been established by mocked or loopback tests.

## Topology and restoration

Announcement eligibility is separate from raw topology inspection. Grouped
coordinators are refused without playback; raw inspection must still accept them
when checking the original group during restoration. Bonded/hidden devices and
unknown topology flags are unsupported and fail closed.

A grouped member is detached and verified as standalone before snapshot/playback.
Before unjoining, its identity and original topology are checked again. Immediately
before playback, its identity and singleton membership are rechecked. Restoration
requires the original coordinator and exact member set, with bounded convergence
polling. Concurrent changes fail closed rather than joining a different group.
Sonos provides no atomic lock against other controllers changing topology during
playback; the pre-playback check cannot eliminate that race.

Cleanup calls `Snapshot.restore(fade=False)` before explicitly restoring volume.
An empty saved URI is cleared on the coordinator. Volume and cleared-URI read-back
errors or mismatches are failures, not successful verification. Cleanup continues
attempting independent restoration steps after failures. Successful API calls are
not proof of frame-accurate queue/seek restoration; brief interruption is inherent.

SIGINT/SIGTERM are deferred during cleanup, with prior handlers restored on exit.
Restoration failure takes precedence over interrupts and playback failures.
Otherwise deferred interrupts propagate, then the original playback failure.

## Completion and error contract

Only observed PLAYING followed by STOPPED counts as completion. TRANSITIONING
alone does not prove playback started. Unknown/malformed telemetry, read errors,
pausing, non-start, and timeout produce `SONOS_PLAYBACK_FAILED` after cleanup.
Deadlines bound polling, not every blocking SoCo or network call.

Normal phase failures print `SONOS_SAY_ERROR=<code>` without exception traces:

- `ELEVENLABS_KEY_FAILED`
- `ELEVENLABS_SYNTHESIS_FAILED`
- `SONOS_DISCOVERY_FAILED`
- `SONOS_TOPOLOGY_FAILED`
- `SONOS_PLAYBACK_FAILED`
- `SONOS_RESTORE_FAILED`

| Exit | Meaning |
| --- | --- |
| 0 | Playback completed and cleanup succeeded |
| 1 | Configuration or callable input validation error |
| 2 | CLI usage error or a fixed phase failure |
| 3 | Unexpected unclassified error; fixed playback marker |
| 10 | SoCo unavailable |
| 130 | SIGINT/KeyboardInterrupt after successful cleanup |
| 143 | SIGTERM after successful cleanup |

Other `SystemExit(N)` values propagate unchanged after successful cleanup.
Signal processing can be delayed by blocking calls. Restoration failures require
manual inspection; do not retry automatically or announce success because speech
may have been audible.
