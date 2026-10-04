---
name: airpods-say
description: >-
  Speak selectively through AirPods using ElevenLabs when the user requests
  speech or enables session-local voice mode. Reviewing or maintaining this
  skill does not activate speech.
---

# AirPods Say

## Speech policy

- Keep the complete response in chat as the durable transcript.
- Speak useful human-facing conclusions, reminders, short questions, and
  completion notices. Summarize long or technical answers naturally.
- Keep tool activity, commands, code, paths, URLs, logs, detailed diagnostics,
  and routine progress updates text-only.
- Honor explicit requests: "say this" speaks the requested content; "read
  everything" reads the full response within the 5,000-character limit.
  "Text only" or "voice off" means do not invoke the helper.
- Never send or speak secrets, passwords, authentication material, API tokens,
  private keys, or similarly sensitive strings.
- Voice mode is session-local and off by default. It does not authorize speaking
  every response. Any future persistence must default off and preserve these
  rules and the output checks.

## Setup and privacy

Requires macOS with CoreAudio/CoreFoundation, Python 3.8+, and `ffplay` from
ffmpeg (`/opt/homebrew/bin/ffplay` or on `PATH`). No third-party Python packages
are required. Deploy `elevenlabs-voice-policy` alongside `airpods-say`; its
`scripts/elevenlabs_voice.py` supplies shared model/stability policy.

Speech text is sent to ElevenLabs for synthesis; this is not local-only TTS.
Explain this during setup and send only content authorized for that processing.

Supply your own configuration in the invoking environment:

```sh
export ELEVENLABS_VOICE_ID='your-voice-id'
export ELEVENLABS_KEYCHAIN_SERVICE='your-keychain-service'
export ELEVENLABS_KEYCHAIN_ACCOUNT='your-keychain-account'
```

Replace these placeholders with your identifiers. The service/account select an
existing macOS Keychain item containing the API key; they are not the key itself.
`--voice-id` overrides `ELEVENLABS_VOICE_ID`. No personal defaults are bundled.
Missing or blank required settings fail with exit 1.

Never print, log, echo, paste, or pass the API key on a command line. Do not
create, modify, or rotate Keychain items during ordinary use. Keep user-specific
configuration outside the skill package.

## Output checks

- The current default output device name must contain "AirPods". Otherwise,
  the helper exits before Keychain access or synthesis.
- It never switches outputs automatically. It polls the output during playback
  and requests a stop when a change or disconnection is detected. These checks
  are not device-bound routing or an instantaneous-stop guarantee.
- Do not fall back to speakers, another device, or a system voice unless the
  user explicitly requests that separate pathway.

## Run

Set `SKILL_DIR` to the absolute directory containing this `SKILL.md`. Resolve
reference files relative to that directory, not the working directory. No
harness-specific placeholder substitution is required.

Run **without a PTY**, using a quoted heredoc to avoid shell interpolation:

```sh
python3 "$SKILL_DIR/scripts/airpods_say.py" - <<'AIRPODS_SPEECH'
Text to speak.
AIRPODS_SPEECH
```

Choose a delimiter absent from the speech as a complete line. Do not launch
stdin mode in a PTY and send a single Ctrl-D: it may flush input without
providing EOF, leaving the helper blocked.

Dry-run checks input, voice configuration, `ffplay`, and active output without
Keychain access, synthesis, or playback. Keychain selectors are not required:

```sh
python3 "$SKILL_DIR/scripts/airpods_say.py" --dry-run 'Test'
```

Require a real exit code of 0 before reporting success. Wrapper timeouts, open
processes, and echoed terminal input are not evidence of completed playback.

## Models and expression

The current code defaults to `eleven_v4` and MP3 streaming, with no stability
setting. Model availability must be checked with your provider account; these
instructions do not establish live compatibility.

For v3 expression tags, explicitly select `--model eleven_v3`. Its default is
Natural stability (`0.5`); `--stability creative` selects `0.0`, and `robust`
selects `1.0`. Omit `--stability` for other models.

Preserve requested tags exactly; do not silently infer, insert, rewrite, or
remove them. For deliberate expressive delivery, consult
`references/audio-tags.md`. This working palette is not an allow-list or a
promise of consistent performance. Test unfamiliar tags; do not automatically
decorate ordinary speech. Accent results vary, and prior `[sings]` experiments
remained spoken rather than melodic.

Other options: `--timeout SEC` sets a positive HTTP timeout; `--debug` enables
diagnostics, including device/configuration metadata, without printing the key.

## Exit handling

- **0:** helper reports completion, or dry-run succeeded.
- **1:** missing/invalid configuration, invalid input, or a general failure.
- **2:** AirPods not active or output changed before playback; remain text-only.
- **3:** output changed/disconnected during playback; do not retry automatically.
- **4:** Keychain credential lookup failed.
- **5:** ElevenLabs request failed.
- **6:** `ffplay` missing.

Report failures concisely in text. Never expose credentials or provider response
bodies. Retain the complete written response regardless of speech outcome.
