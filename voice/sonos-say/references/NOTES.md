# Sonos Say — Technical Notes

## Dependencies and configuration

The implementation uses Python's standard library, SoCo, and the sibling
`elevenlabs-voice-policy/scripts/elevenlabs_voice.py`. Package both skills at the
same directory level for copied installations. Whole-skill symlinks resolve
the policy next to the real checkout; no separate policy symlink is necessary.
The shared module owns `DEFAULT_MODEL_ID` (`eleven_v4`), v3 stability presets,
strict ID/payload rules, bounded Keychain lookup, redirect refusal, and MP3 MIME
validation. Consumer adapters retain their own exit/phase error contracts; the
module contains no user voice IDs or credential selector defaults.

- `--voice-id` overrides `ELEVENLABS_VOICE_ID`; no bundled voice.
- `--room` overrides `SONOS_ROOM`; no implicit destination.
- Normal execution requires `ELEVENLABS_KEYCHAIN_SERVICE` and
  `ELEVENLABS_KEYCHAIN_ACCOUNT`. They select an existing Keychain item.
  The key is retrieved through `/usr/bin/security find-generic-password` with
  a ten-second timeout. Lookup errors omit selector values and subprocess stderr.
- Help needs no configuration. Dry-run needs room/voice but no Keychain selectors.
  It validates and describes the request without network, credentials, discovery,
  or speaker access. Its output includes the submitted text, so treat it accordingly.
- Text, identifiers, volume, timeout, and model/stability combinations are validated
  before sensitive I/O. Explicit playback and TTS timeouts must be finite and
  positive, including direct calls. Default playback is calculated after synthesis:
  `ceil(MP3 bytes / 16000) + 30` seconds for `mp3_44100_128`. Explicit `--timeout`
  is a hard playback override. Independent `--tts-timeout` defaults to 180 seconds.

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

1. Validate all configuration; install scoped, cleanup-aware signal handling.
2. Discover the room by case-insensitive name; reject missing/ambiguous matches.
3. Freshly inspect topology and reject unsafe targets and cloud queue sources
   on the target or its playback owner, before credentials or synthesis.
4. Read the selected Keychain item; synthesize the exact text to temporary MP3.
5. Freshly validate again; arm recovery immediately before unjoin and isolate.
6. Capture transport, volume, and mute; reject a source that changed to cloud.
7. Start the restricted HTTP server and AVTransport recorder; recheck isolation
   and source, set volume, temporarily unmute, arm the attempt, and play.
8. Require correlated PLAYING then STOPPED via events or fresh transport polling.
9. Stop the owned event subscription and server; restore state and exact grouping.
10. Remove temporary files, restore prior signal handlers, then report outcome.

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
same-origin redirects. Non-200, non-`audio/mpeg`, and empty responses are not
saved as audio. Raw header/body socket errors are sanitized. Errors
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

Each independent inspection clears `zone_group_state`'s five-second polling
cache before reading the group. Eligibility uses that same sampled membership.
This refreshes initial/pre-unjoin/pre-playback checks and every convergence
sample, including coordinator-side restoration checks. Cache-clear/read failures
fail closed. Active topology subscriptions bypass polling even after invalidation
in SoCo 0.31.2; they are unsupported and refused rather than treated as fresh.
Offline cache-aware tests do not establish firmware compatibility.

A grouped member is detached and verified as standalone before snapshot/playback.
Before unjoining, its identity and original topology are checked again. Immediately
before playback, its identity and singleton membership are rechecked. Restoration
accepts an already exact original coordinator/member set without a join RPC.
Otherwise it joins only from the expected isolated-target/original-group-minus-
target state. Convergence polls every 0.5 seconds for at most five seconds, always
invalidating the cache and sleeping no longer than the remaining deadline.
Concurrent changes fail closed rather than joining a different group.
Sonos provides no atomic lock against other controllers changing topology during
playback; the pre-playback check cannot eliminate that race.

Cleanup calls `Snapshot.restore(fade=False)` before explicitly restoring volume.
An empty saved URI is cleared only on idle standalone targets, not detached
members awaiting regrouping. Mute, volume, source/transport where meaningful,
and cleared-URI read-back errors or mismatches are failures. Queue source checks
use `GetMediaInfo.CurrentURI`, not a per-track URI. Cleanup continues
attempting independent restoration steps after failures. Successful API calls are
not proof of frame-accurate queue/seek restoration; brief interruption is inherent.

SIGINT/SIGTERM handling is installed before live preflight, including direct
`announce()` callers. Signals are deferred through server shutdown, restoration,
and temporary-directory exit; prior handlers are restored only after deletion.
Restoration failure takes precedence over interrupts and playback failures.
Otherwise deferred interrupts propagate, then the original phase failure.
One cleanup-aware guard spans the full resource lifetime. Cleanup actions and
snapshot restoration run once, without interrupt-driven replay.
A failed pre-unjoin validation incurs no recovery obligation or group RPC;
a validated unjoin attempt does, even if it mutates the device then times out.

## Completion and error contract

Only observed PLAYING followed by STOPPED counts as completion. TRANSITIONING
alone does not prove playback started. Unknown/malformed telemetry, read errors,
pausing, non-start, and timeout produce `SONOS_PLAYBACK_FAILED` after cleanup.
Deadlines bound polling, not every blocking SoCo or network call.

An attempt-scoped AVTransport subscription starts before Play. A bounded,
thread-safe queue sink retains ordered states during blocking RPCs, correlated
to the announcement URI and subscription sequence. Initial, stale, duplicate,
and unrelated evidence cannot establish start; overflow, sequence loss,
subscription expiry, and unsupported backends fail closed. Only the default
threaded SoCo backend is supported. Each attempt owns a callback listener using
the SDK's SID registry. Cleanup unsubscribes only this attempt, bounds the SDK
0.31.2 shutdown wake-up request, and closes its socket without stopping another
caller's listener or subscriptions. No
ZoneGroupTopology subscription or automatic firewall change is made.

Cloud queues (`x-rincon-queue:…#<nonzero>`) are refused with
`SONOS_SOURCE_UNSUPPORTED`; SoCo 0.31.2's Snapshot cannot restart them. Checks run
in read-only preflight, before isolation, after snapshot, and before override.
If isolation occurred but source replacement did not, cloud refusal regroups
only: it never invokes cloud Snapshot restore/play.

### Pending live acceptance

Findings 8 and 10 remain hardware-gated: idle standalone empty-URI clearing
and real short-clip AVTransport delivery need separately approved firmware and
callback/firewall checks. A rejected clear remains a restoration failure.
Loopback SDK, dummy-decoder, and mocked speaker evidence do not prove audible
playback or firmware support. Hold merge readiness unless live checks pass or
Kyle approves explicit supported-source/short-clip restrictions.

Normal phase failures print `SONOS_SAY_ERROR=<code>` without exception traces:

- `ELEVENLABS_KEY_FAILED`
- `ELEVENLABS_SYNTHESIS_FAILED`
- `SONOS_DISCOVERY_FAILED`
- `SONOS_TOPOLOGY_FAILED`
- `SONOS_SOURCE_UNSUPPORTED`
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
