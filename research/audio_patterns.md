# Audio patterns

> **Limitation (important):** no audio stream could be downloaded from this environment
> (YouTube bot-check + PO-token gating), and transcripts were not retrievable. Nothing in this
> file is a *measurement* of the reference audio. Every statement is an inference from public
> metadata and burned-in captions, and is marked with its evidence and confidence.

## A1 — Dialogue carries the story; no narrator (Era B) — medium–high confidence

Evidence: in every sampled Era B timeline the caption is a line of character dialogue in the
first person, matching the face on screen ("I'M SO SORRY SIR", "HE STOLE MY WALLET",
"I'M YOUR NEW BOSS"). No third-person narration captions were observed.
Era A captions are third-person narration ("THE WOMAN WAS A NURSE") → narrator voice-over.

## A2 — One recurring music bed — medium confidence

All 41 Era B descriptions credit the same track ("Track: I'm god") and use edit hashtags
#PhonkEdit #DarkEdit #EmotionalScene. Inference: a single dark/atmospheric music bed under
the dialogue across the whole era. Level, ducking and placement are unknown.

Engine policy: original procedurally generated music beds (dark, slow, atmospheric) or
user-licensed tracks only, ducked −14 dB to −18 dB under dialogue; never the referenced track.

## A3 — Speaking pace — low confidence (estimated)

Burned-in captions show 2–4 words per caption and a new caption in ~60 % of 2 s samples,
consistent with conversational dialogue pace (~140–170 words/min). Engine default: 150 wpm
with ±10 % per character, configurable.

## A4 — Emotional intensity — low confidence

Frames show raised-voice confrontation faces during escalation and calmer close-ups at the
end. Engine maps beat → delivery: setup neutral, escalation tense/angry for the antagonist,
reveal firm/calm for the protagonist, ending relieved.

## A5 — Sound effects — unknown

Not observable. Engine uses sparse original SFX (whoosh on accent transitions, low impact on
the reveal) at modest levels; they can be disabled in settings.

## A6 — Voice identity

The reference uses the original actors' voices from third-party footage. The engine uses
distinct synthetic voices per character via TTS adapters and never clones any real person.
