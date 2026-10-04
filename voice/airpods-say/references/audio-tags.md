# Eleven v3 expressive tag reference

This is a curated working reference for Eleven v3 TTS. It is not an official
allow-list: support varies by voice, wording, context, stability, and generation.
Observations below come from prior informal experiments, not validation across
voices or provider versions. Select `--model eleven_v3` when using this palette
with the speech helper.

## Usage

- Prefer Creative stability for acting experiments; use Natural or Robust for ordinary speech.
- In prior experiments, tags tended to affect short phrases. Keep cues local
  and test their reach rather than assuming a fixed number of words.
- Use tags sparingly and only when the user explicitly requests expressive
  delivery or the cue adds clear meaning.
- Test unfamiliar tags before relying on them in an important spoken message.
- Exact bracketed wording is sent to the model; do not silently rewrite user-supplied tags.
- Treat Creative output as stochastic: one successful generation does not guarantee the next one will match.

## Starting points

These tags appeared in prior successful experiments or reference material.
Their behavior is not guaranteed with another voice or generation:

- `[laughs]`
- `[whispers]`
- `[sighs]`
- `[slow]`
- `[excited]`
- `[amazed]`
- `[nostalgic]`
- `[frustrated]`
- `[villain voice]`
- `[giggles]`
- `[loudly]`
- `[voice cracks]`
- `[pauses]`
- `[Southern US accent]` — reported working in informal tests; strength may vary
- `[Irish accent]` — reported working in informal tests; strength may vary

## Promising or inconsistent

These produced plausible differences but were not reliable or rigorously isolated:

- `[English London accent]` — worked in one generation and failed in another
- `[visibly shaken]`
- `[barely excited]`
- `[laughs loudly]`
- `[clears throat]`
- `[trying very hard not to laugh during a serious announcement]`
- `[celebrating quietly because everyone else is asleep]`
- `[exhausted but proud after shipping to production]`

Arbitrary natural-language directions appear to work best for emotion, energy, pace, restraint, breath, and vocal texture.

## Known boundaries

- Accent control is tag-, voice-, and generation-dependent. In prior informal
  tests, Southern and Irish worked, English London was inconsistent, and French
  and Australian did not work. These observations do not predict other voices;
  other accents remain unverified here.
- `[sings]` remained spoken rather than melodic in prior tests. Actual singing
  requires a separate, explicitly requested capability; none is bundled here.
- Character directions may become theatrical or shift vocal identity; use them experimentally.

## Community-sourced candidate palette

The following community-sourced candidates are grouped for discovery. Their
individual support and original provenance have not been independently verified.


## EMOTIONAL STATES

### Core emotions

- `[happy]`
- `[sad]`
- `[angry]`
- `[excited]`
- `[nervous]`
- `[confident]`
- `[calm]`
- `[serious]`
- `[playful]`
- `[worried]`
- `[frustrated]`
- `[hopeful]`
- `[relieved]`
- `[disappointed]`
- `[embarrassed]`
- `[lonely]`
- `[grateful]`
### Complex emotional shades

- `[nostalgic]`
- `[wistful]`
- `[bittersweet]`
- `[melancholic]`
- `[anxious]`
- `[tense]`
- `[uneasy]`
- `[determined]`
- `[triumphant]`
- `[resigned]`
- `[reflective]`
- `[skeptical]`
- `[suspicious]`
- `[jealous]`
- `[ashamed]`
- `[yearning]`
### Intensity variants

- `[slightly nervous]`
- `[barely holding back anger]`
- `[quietly emotional]`
- `[overjoyed]`
- `[deeply sorrowful]`
- `[barely excited]`
- `[visibly shaken]`

## REACTIONS AND HUMAN SOUNDS

### Breath and body reactions

- `[gasp]`
- `[sigh]`
- `[deep breath]`
- `[sharp inhale]`
- `[exhale slowly]`
- `[breath trembles]`
- `[nervous breath]`
- `[breath catches]`
### Speech reactions

- `[chuckles]`
- `[laughs softly]`
- `[laughs loudly]`
- `[nervous laugh]`
- `[snorts]`
- `[giggles]`
- `[scoffs]`
- `[groans]`
- `[mutters]`
### Shock and surprise

- `[gasps in disbelief]`
- `[stunned silence]`
- `[taken aback]`
- `[whispers in shock]`
### Eating and mouth sounds

- `[swallows]`
- `[gulps]`
- `[clears throat]`
- `[lips smack]`
- `[clicks tongue]`

## VOLUME AND ENERGY

### Low energy

- `[whispering]`
- `[quietly]`
- `[softly]`
- `[hushed tone]`
- `[murmuring]`
### Neutral

- `[natural tone]`
- `[casual tone]`
- `[conversational tone]`
### High energy

- `[loudly]`
- `[shouting]`
- `[calling out]`
- `[energetically]`
- `[bursting with excitement]`
### Intensity shifts

- `[voice rising]`
- `[voice lowering]`
- `[voice cracks]`
- `[voice trembling]`

## PACING AND RHYTHM

### Speed control

- `[rushed]`
- `[rapid-fire]`
- `[slow and deliberate]`
- `[slows down]`
- `[picks up pace]`
### Natural conversation rhythm

- `[pauses]`
- `[brief pause]`
- `[long pause]`
- `[hesitates]`
- `[stammers]`
### Speech patterns

- `[drawn out]`
- `[trails off]`
- `[cuts sentence short]`
- `[repeats for emphasis]`
### Breathing rhythm

- `[speaks between breaths]`
- `[breathing heavily]`

## EMPHASIS AND DELIVERY

### Word emphasis

- `[emphasized]`
- `[stress on next word]`
- `[strong emphasis]`
- `[soft emphasis]`
### Subtle delivery styles

- `[understated]`
- `[deadpan]`
- `[dry tone]`
### Persuasive speaking

- `[convincingly]`
- `[earnestly]`
- `[passionately]`
### Authority and command

- `[firmly]`
- `[commanding tone]`
- `[assertively]`

## CHARACTER PERFORMANCE

### Age cues

- `[childlike tone]`
- `[teenager tone]`
- `[young adult voice]`
- `[middle-aged tone]`
- `[elderly voice]`
### Character archetypes

- `[heroic voice]`
- `[wise mentor voice]`
- `[villain voice]`
- `[evil scientist voice]`
- `[news reporter voice]`
- `[storyteller voice]`
- `[radio host voice]`
- `[teacher voice]`
### Fantasy characters

- `[pirate voice]`
- `[knight voice]`
- `[royal voice]`
- `[dragon narrator]`
### Sci fi characters

- `[robotic tone]`
- `[sci-fi AI voice]`
- `[hologram voice]`
- `[cybernetic voice]`

## GENRE PERFORMANCE

### Narrative styles

- `[fantasy narrator]`
- `[epic narrator]`
- `[documentary narrator]`
- `[audiobook narrator]`
### Film styles

- `[classic film noir]`
- `[thriller narrator]`
- `[horror whisper]`
- `[comedic narration]`
### Advertising styles

- `[commercial voice]`
- `[enthusiastic ad voice]`
- `[luxury brand voice]`
- `[corporate presentation tone]`

## CONVERSATIONAL REALISM

### Thinking sounds

- `[thinking]`
- `[muttering to self]`
- `[searching for words]`
### Awkward pauses

- `[awkward silence]`
- `[hesitates nervously]`
### Interruptions

- `[interrupting]`
- `[cuts in]`
### Dialogue dynamics

- `[leans closer]`
- `[steps back slightly]`

## EMOTIONAL SHIFTS

### Sudden changes

- `[suddenly serious]`
- `[suddenly excited]`
- `[tone darkens]`
### Gradual changes

- `[becoming emotional]`
- `[voice softens]`
- `[anger building]`
### Reveals

- `[realization dawning]`
- `[whispers revelation]`

## ACCENTS AND DIALECTS

### English variants

- `[American accent]`
- `[British accent]`
- `[Australian accent]`
- `[Indian English]`
- `[Irish accent]`
- `[Scottish accent]`
- `[Canadian accent]`
### European accents

- `[French accent]`
- `[German accent]`
- `[Italian accent]`
- `[Spanish accent]`
- `[Russian accent]`
### Regional US accents

- `[Southern US accent]`
- `[New York accent]`
- `[Midwestern accent]`
### Character accents

- `[pirate accent]`
- `[medieval accent]`

## NARRATIVE STORY BEATS

### Scene transitions

- `[after a moment]`
- `[after a long pause]`
### Memory and reflection

- `[softly remembering]`
- `[nostalgic tone]`
### Suspense

- `[whispers]`
- `[ominous tone]`
### Revelation

- `[dramatic reveal]`
- `[whispers intensely]`
### Ending tones

- `[soft conclusion]`
- `[hopeful ending]`
- `[quiet reflection]`

## CINEMATIC VOICE DIRECTION

### Camera style acting

- `[intimate whisper]`
- `[voice close to microphone]`
### Large scale cinematic

- `[epic cinematic tone]`
- `[grand narration]`
### Emotional monologue

- `[breaking emotionally]`
- `[voice trembling with emotion]`

## ADVANCED PERFORMANCE CUES

### Psychological cues

- `[masking fear]`
- `[forced calm]`
### Physical state

- `[out of breath]`
- `[exhausted voice]`
- `[in pain]`
### Social cues

- `[polite tone]`
- `[awkward politeness]`
