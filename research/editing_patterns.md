# Editing patterns

Source: storyboard frames of all 91 Shorts (`frame_metrics.json`), full timelines of
`4-WzLJxiPJk` (top tier) and `TTqUFtjdBLE` (bottom tier). Storyboards sample one frame every
1.96 s (Era B) / 0.99 s (Era A): **cut rates are lower bounds**.

## E1 — Shot length (consistent, medium confidence)

| Metric (median) | Era A | Era B |
|---|---|---|
| sample intervals with a visual change | 57.7 % | 72.7 % |
| changes / minute (lower bound) | 35.5 | 22.3 |
| mean shot length (upper bound) | ≤ 1.69 s | ≤ 2.69 s |

Era B timelines show a new camera angle in almost every 2 s sample during dialogue
(shot/reverse-shot). The engine targets **2.6 s mean shot length** with variation 1.2–4.5 s,
shorter during escalation and reveal.

Cut rate does not differ across performance tiers (top 22.5 vs bottom 22.1 changes/min,
ρ = −0.02): it is a format constant.

## E2 — Shot grammar (consistent, medium confidence)

From the two full timelines:

* 0–4 s: medium/wide two-shot (with POV bar).
* Setup and escalation: alternating single close-ups/medium close-ups of the speaker
  (shot/reverse-shot), occasional over-the-shoulder.
* Authority/turn (~60–78 %): wider group shots (e.g. police arrive at 102–122 s in `4-WzLJxiPJk`).
* Reveal (~75–90 %): tighter close-ups, a blended/flash transition frame was observed at the
  reveal in `TTqUFtjdBLE` (146 s).
* End: close-up reaction (33/41), fade to black (4/41), medium (4/41).

Estimated shot mix (from timelines): ~55 % single close-ups, 20 % medium singles, 12 % two-shots,
8 % wide/group, 5 % inserts.

## E3 — Transitions (consistent, medium confidence)

Hard cuts dominate. Accent transitions (flash/cross-dissolve) appear around the reveal.
No wipes, slides, or glitch transitions were observed in the sampled frames.

## E4 — Narration–visual relationship

* Era B: no narrator. The image on screen is the *speaker* of the caption in ~all sampled
  dialogue frames (captions are the speaker's line) → the edit follows the dialogue.
* Era A: narrator voice-over over film footage; single-word captions; cuts ~1.7 s.

## E5 — Zoom / camera movement

Not measurable from 2 s samples (limitation). Frames show stable framing between consecutive
samples of the same shot, so heavy zoom effects are unlikely. Because the engine works from
stills/generated clips, it **simulates** camera motion with subtle push-ins (1.00→1.06 scale over
a shot) and occasional slow pans; strength is configurable and kept subtle.

## E6 — Persistent elements

* Title bar 0–~5 s (41/41).
* Small channel handle watermark bottom-centre (~92 % height) throughout (observed in all Era B
  sampled frames). The engine uses an optional configurable brand handle — never the
  reference handle.

## Engine parameters derived (see `channel_dna.json`)

```
mean_shot_s: 2.6       shot_s_range: [1.2, 4.5]
transition: hard_cut   accent_transition: flash_white@reveal
opening: medium_two_shot   ending: closeup_reaction
title_bar_s: 5.0       motion: subtle push-in 4-6 %
```
