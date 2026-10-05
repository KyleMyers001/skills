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
5. Freshly validate before unjoin and isolate ordinary grouped members, arming
   recovery at the RPC boundary. Ungrouped bonded primaries keep their bonds.
6. Recheck the exact logical room; capture transport, volume, and mute; reject a
   source that changed to cloud.
7. Start the restricted HTTP server and AVTransport recorder; recheck the room
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

Announcement eligibility is separate from raw physical-group inspection. True
multi-room coordinators are refused without playback; raw inspection must still
observe them when checking the original group during restoration. An ungrouped
home-theater primary with explicitly owned satellites is one logical room, not a
multi-room group. Only the visible, non-satellite primary receives transport calls;
no join/unjoin or bond configuration calls are made for this layout. Bonded rooms
with another logical room, direct satellite/hidden targets, stereo pairs, unknown
primary flags, and missing/contradictory ownership remain unsupported.

`TopologyPlan` retains raw member UID sets for ordinary regrouping, frozen role
observations including parent UIDs, and the selected room's member UID set. It
stores neither mutable member objects nor redundant satellite-parent sets.
SoCo 0.31.2 parsed roles use private `_uid`, `_is_satellite`, `_has_satellites`,
and `_satellite_parent`, plus the processed state's visibility set. That access
is isolated and covered by real-parser subprocess contracts. Primary-only
`has_satellites` flags on satellites do not establish ownership. Room names and
visibility alone never prove a bond.

Each independent inspection fetches one `GetZoneGroupState` payload directly,
with explicit empty arguments and the check's timeout, and processes it into the
shared state; an unknown household ID is fetched the same way. SoCo 0.31.2
composes implicit arguments by downloading the service description under a fixed
10-second timeout, so its no-argument getters are not used for observation. The
processed payload primes SoCo's cache so the group lookup reads that sample
without polling again. A 501 response (SoCo documents this for households of
about 20+ players) fails immediately: SoCo's ZGT event fallback would open a
topology subscription, which this skill never does. Eligibility uses copied roles
from that same processed payload without public role getters, which can poll
individual satellites and introduce new observations. No surround/sub needs to
answer a separate role request. A changed `processed_count` during copying, or
unavailable observation identity, still fails closed. This fetches fresh state for
initial/pre-unjoin/pre-snapshot/pre-playback checks and every convergence
sample, including coordinator-side restoration checks. Read/processing failures
fail closed. Active topology subscriptions can replace polled state at any time
in SoCo 0.31.2; they are unsupported and refused rather than treated as fresh.
Offline cache-aware tests do not establish firmware compatibility.

A grouped member is detached and verified as standalone before snapshot/playback.
Before unjoining, its identity and original topology are checked again. Immediately
before snapshot and playback, its identity and exact selected-room membership
are rechecked: singleton for an ordinary isolated speaker, primary plus the
original owned satellites for a bonded room. Restoration
accepts an already exact original coordinator/member set without a join RPC.
Otherwise it joins only from the expected isolated-target/original-group-minus-
target state. Convergence polls every 0.5 seconds for at most five seconds, always
fetching a fresh payload and sleeping no longer than the remaining deadline.
Unavailable target/coordinator observations retry within that pre-join deadline.
`restore_target_group`'s timeout applies separately to the pre-join wait, the join
RPC, and post-join convergence, which does not reuse leftover pre-join time.
During cleanup, the enclosing absolute cleanup deadline also caps every regroup
phase. Before/after-snapshot and final isolation checks carry the absolute local
deadline into their poller: both retry requests and sleeps stop at that boundary,
not merely at service dispatch. An exhausted outer deadline performs no read;
zero-timeout single-observation behavior remains unchanged when time is available.
A missing group view is `TopologyIncomplete`. Isolation checks fail on it
immediately; post-unjoin, pre-join, and post-join convergence waits may retry it
because a group change can briefly omit the target from the published groups.
Malformed roles/XML, unsupported polling (including UPnP/HTTP 501), active
subscriptions, SDK protocol errors (including UPnP 401/402), and arbitrary runtime
errors fail immediately, even in convergence waits. Only explicit connection/reset/
timeout exception types are temporary; neither `OSError` nor `SoCoException` is
blanket-retryable. Identity changes and unrelated topology also fail immediately.
Each new read needs at least half a second remaining: skip the read below that
threshold, never round its timeout beyond the deadline. An on-time response can
still succeed with less than half a second left. Each dispatched read passes the
remaining check time as its request timeout, capped at five seconds. Sequential
target/coordinator reads share the pre-join budget; household lookup and topology
fetch share one observation deadline. An exhausted budget starts no further read,
and a result arriving at or after its deadline is rejected. Topology reads leave
SoCo's global configuration untouched; only cleanup RPCs and the join RPC set its request
timeout and disable `ZGT_EVENT_FALLBACK`, restoring both afterward.
Observed unrelated topology fails immediately without joining. Sonos provides
no atomic lock against other controllers changing topology during playback;
fresh checks cannot eliminate the race between verification and a write.

Cleanup freshly verifies exact isolated membership and satellite ownership for
**every target**, including standalone and detached ordinary speakers, before
restoration. Detached members restore only local rendering via Snapshot's member
path (temporarily suppressing its captured coordinator flag, restoring the flag
on every exit). This preserves mute, volume, EQ and fixed-volume handling without
transport reads, source replacement, pause/play/stop, or queue seeking before
rejoin. Exact group recovery provides its own fresh checks. Standalone/bonded
targets use full `Snapshot.restore(fade=False)` and verify topology again afterward. The post-restore
sample guards empty-URI clearing. Standalone and bonded targets then get one more
isolation check after the last cleanup write. Do not run that check after a
rejoin, and do not repair the room; a later regroup is `SONOS_RESTORE_FAILED`.
No public coordinator lookup is used for a captured plan. Unavailable observations
retry every 0.5 seconds for at most five seconds per check. Known unsafe topology
is never retried into acceptance, and mutating RPCs are never replayed. If topology
is unsafe or remains unavailable, cleanup skips transport writes and does not join,
attempts safe independent target-local mute/volume recovery, and reports
`SONOS_RESTORE_FAILED`. It does not repair bonds or join an unrelated group.
Cleanup starts no further step after 45 seconds. When a detached member awaits
regrouping, local restoration reserves 15 seconds for the pre-join wait, join,
and post-join wait. A scoped `soco_deadline` guard intercepts `send_command` on
only the target's AVTransport, RenderingControl, ZoneGroupTopology, and
DeviceProperties service instances. It checks the absolute deadline before every
RPC, including Snapshot's internal calls and `finally` rendering restoration.
The request timeout is the smallest of five seconds, remaining time, and any
stricter configured or explicit timeout. With less than half a second remaining,
no new target RPC starts. Known implicit zero-argument topology/household reads
use explicit empty arguments to avoid an uncapped service-description fetch.
Prior instance dispatch attributes and SoCo configuration are restored on every
exit, including partial setup and interruption; no SDK class is patched.

Snapshot remains responsible for source, queue, EQ, and transport semantics,
except its SoCo 0.31.2 local-queue restart is scoped to a metadata-free equivalent
of `play_from_queue`: retain the coordinator gate, set `x-rincon-queue:<uid>#0`,
seek the same one-based track, and honor `start`. The UID comes from the fresh
processed topology that authorized cleanup. The SDK's original path calls
`get_speaker_info()` when its metadata cache is empty, which performs an unrelated
raw `requests.get(..., timeout=None)` and XML parse outside the SOAP guard. No
device-description read or partial metadata-cache prefill is needed for a local
queue URI. The scoped instance method is restored on every exit; SOAP dispatch
still guards every queue/seek/play action. Installed-SoCo subprocess tests exercise
real Snapshot, role getters and processed topology with an empty metadata cache.
Exhaustion stops further local RPCs and reports `SONOS_RESTORE_FAILED`; regrouping
gets its own guard and still requires fresh safety evidence. It cannot turn a
partial restoration into success. Compound URI clearing and read-back steps use
the same dispatch guard. No mutating worker continues in the background.
Socket timeouts are not whole-exchange deadlines: an in-flight trickling SOAP
response can exceed the budget. This prevents cumulative RPC starvation, not
arbitrary wall-clock overruns, and does not enable topology-event fallback.

Device-description is not fetched before a topology-backed restore or rejoin.
A fresh topology poll that still reports the captured UUID authorizes transport,
volume, mute, and rejoin; do not cross-check the description. Fetch the UDN from
the target IP's `/xml/device_description.xml` on port 1400 only to authorize local
rendering when topology is unavailable or unsafe (including detached EQ recovery).
SoCo's `uid` and even
`get_speaker_info(refresh=True)` reuse the cached UID and cannot authorize those
writes. A cached device-UID change alone is not a transport refusal. A different
topology UUID is still refused. Identity reads have a five-second socket timeout,
a 64 KiB body limit, strict UDN parsing, and no redirect/proxy following. An
encoding-aware Expat preflight rejects DOCTYPE/entity declarations and external
entity references before expansion; only declaration-free, well-formed bytes
reach ElementTree UDN extraction. Both parsers receive the same bytes. UTF-8 and
UTF-16 (either byte order) remain supported; no new dependency is used. Socket
timeouts restart on every received byte, so the request runs in a read-only worker
with one deadline for the whole exchange: the earlier of five seconds or the
absolute local-cleanup deadline. Late identity results cannot authorize writes,
and fallback cannot spend a detached target's regroup reserve. Cancellation shuts
down the retained socket even after a `Connection: close` response takes ownership
from `HTTPConnection`. The worker closes its response, and cleanup joins that
worker before returning; it does not leave a trickling reader/socket behind.
A failed or mismatched description skips local writes. A successful snapshot
already restores mute; cleanup does not redundantly set it again. Volume is
written explicitly because it was captured separately and is read back.

A pre-mutation refusal skips transport/rendering restoration for every layout;
an attempted unjoin still receives regroup-only recovery. State recovery is armed
immediately before the first rendering RPC because it may apply a change and then
fail.

Safe cleanup calls `Snapshot.restore(fade=False)` before explicitly restoring
volume. An empty saved URI is cleared on idle standalone/bonded targets, not detached
members awaiting regrouping. Missing-plan preflight cleanup is read-only; an
internal caller claiming mutation without a captured plan fails closed with no
writes. There is no role-only cleanup authorization path.
Mute, volume, source/transport where meaningful, and cleared-URI read-back errors
or mismatches are failures. Queue source checks
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

### Cleanup follow-up design boundaries

- **General implicit service metadata (#5), deferred:** only the known
  `GetZoneGroupState`/`GetHouseholdID` zero-argument calls receive explicit `[]`.
  Arbitrary future `send_command(action, args=None)` calls can still make SoCo
  fetch service descriptions under its fixed 10-second timeout. A generic args
  substitution would be unsafe for actions with required arguments. Audit new
  SDK call paths; a general metadata policy requires separate design/tests.
- **Hard SOAP deadlines (#11), deferred:** per-RPC timeouts and boundary checks
  are soft limits, not cancellation of a trickling in-flight SOAP response.
  Never move mutating RPCs to an abandoned worker to claim a hard deadline.
  Hard cancellation needs a design that establishes whether a mutation applied
  and prevents late writes. The existing read-only identity cancellation,
  retained-socket shutdown and worker join remain intact; these security
  protections must not be removed as a simplification.
- **Final isolation check (#7), retained:** it is not redundant in the current
  ordering. Explicit volume recovery occurs after the post-Snapshot sample;
  another controller can regroup during that interval, and URI clearing can
  also mutate transport. Removing the final sample failed the existing
  `test_later_regroup_after_post_restore_check_fails_without_join` contract.
  Keep verification after these writes and never run it after exact rejoin.

All follow-up verification is offline (real SoCo 0.31.2 subprocess contracts,
mocked SOAP and loopback identity servers). It does not authorize LAN playback or
establish hardware acceptance. `soco-zgs-cache-gotcha.md` was not present anywhere
inside this checkout (including hidden directories); no profile or external
memory copy was read or modified.

## Completion and error contract

Only observed PLAYING followed by STOPPED counts as completion. TRANSITIONING
alone does not prove playback started. Unknown/malformed telemetry, read errors,
pausing, non-start, and timeout produce `SONOS_PLAYBACK_FAILED` after cleanup.
Playback deadlines bound polling, not every discovery or synthesis call.
Topology polls and cleanup RPCs use socket timeouts of at most five seconds.
Cleanup starts no further step after 45 seconds and guards individual target RPCs
against the applicable local or overall deadline; these are not hard wall-clock
completion guarantees for a trickling SOAP response.
Households whose `GetZoneGroupState` answers 501 fail with `SONOS_TOPOLOGY_FAILED`.

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

Bonded-room playback, unchanged bonds, room-scoped HTTP fetching, and actual
TV/HDMI source restoration need separately approved live acceptance. Real SDK
parser and synthetic TV-source Snapshot contracts establish software behavior,
not firmware compatibility or audible playback.

Findings 8 and 10 remain hardware-gated: idle empty-URI clearing and real short-clip
AVTransport delivery need separately approved firmware and callback/firewall checks.
A rejected clear remains a restoration failure.
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
