# Hook patterns

Evidence: titles of all 91 Shorts, first frames of all 41 Era B Shorts (storyboard frame 0),
full timelines of `4-WzLJxiPJk`, `TTqUFtjdBLE` and `c8n65RTCZz8`. Performance = age-adjusted
score (log-view residual after controlling for age; 0 = era median).

## H1 — The lesson is the hook, shown twice (consistent, high confidence)

* **Observation:** Era B titles are the moral of the story in quotes, followed by `☠️🔥 #Trending #Shorts`
  (41/41). The same moral appears burned into the video as `POV: <moral>` in a white bar at the top
  of the frame in **41/41** first frames, staying on screen for the first ~4–6 s
  (measured: present in frames at 0 s, 1.96 s, 3.9 s; gone by 5.9 s).
* **Examples:** `c8n65RTCZz8` "Being young doesn't mean you lack talent" / "POV: BEING YOUNG DOESN'T MEAN YOU LACK TALENT";
  `4-WzLJxiPJk` "He taught his child the most important lesson".
* **Measured:** moral length median 8 words (4–15), median 48 characters.
* **Mechanism (inference):** the viewer knows the *lesson* but not *how* it will be delivered — the
  curiosity gap is "who is going to get taught this lesson, and how?".

## H2 — Cold open inside the conflict (consistent, high confidence)

* **Observation:** frame 0 is mid-scene with characters already in a social situation; no logo,
  no intro, no narrator.
* **Measured by opening shot type (Era B):**

| Opening shot | n | age-adj median |
|---|---|---|
| medium two-shot (two characters in tension) | 14 | **+0.43** |
| medium group | 5 | +0.28 |
| single close-up | 3 | −0.01 |
| medium single | 9 | −0.61 |
| wide establishing | 4 | −0.59 |
| insert/detail (hands, object, shoes) | 6 | **−1.90** |

* **Confidence:** medium — consistent direction, small groups.
* **Engine rule:** open on a medium two-shot of the protagonist and antagonist; never open on an insert.

## H3 — Moral grammar (occasional differences, low confidence)

| Title grammar | n | age-adj median | Examples |
|---|---|---|---|
| general maxim | 19 | +0.28 | "A badge doesn't let you do whatever you want" (2.1 M) |
| question | 1 | +0.72 | "Is a mother like this really a good mother" |
| narrative teaser (he/she…) | 11 | −0.31 | "She thought he was blind, but he saw everything" (3.3 M) |
| imperative (Never/Don't/Please…) | 10 | −0.39 | "Never judge someone by their appearance" (74 K) |

Distributions overlap heavily (top-5 contains every grammar except imperative). Engine treats
this as a soft preference only.

## H4 — Theme of the lesson (occasional, low–medium confidence)

| Theme | n | age-adj median | Top example |
|---|---|---|---|
| parenting / family | 2 | +1.41 | "He taught his child the most important lesson" (3.28 M) |
| kindness / goodwill | 6 | +0.84 | "True hospitality means welcoming everyone with kindness" (1.87 M) |
| greed vs honesty | 2 | +0.76 | "Greed makes you lose everything, while honesty always pays off" (3.05 M) |
| respect / authority | 6 | 0.00 | "A badge doesn't let you do whatever you want" (2.10 M) |
| prejudice / appearance | 10 | −0.18 | "Being young doesn't mean you lack talent" (25.6 M) |
| slander / false accusation | 2 | −0.64 | |
| entitlement / boundaries | 5 | −1.23 | "Don't blindly give in to passengers' unreasonable demands" (30 K) |

Prejudice/appearance is the most frequent theme and produced the outlier, but its median is
below era median — the theme alone does not explain success.

## H5 — Setting as part of the hook (occasional, medium confidence)

Airplane cabin is the most used setting (7/41) and best median (+1.02). Retail store (6, +0.31)
and street (4, +0.17) follow; restaurants (4, −0.64) and jewelry stores (2, −0.88) under-perform.
Inference: settings with a strict status hierarchy (first class vs economy, staff vs customer)
make the injustice legible within the first seconds.

## Hook generation rules for the engine (derived)

1. Hook text = moral aphorism, 6–10 words, general-maxim grammar by default.
2. Show `POV: <moral>` bar 0–5 s; first spoken line within 0.5 s.
3. First shot: medium two-shot of protagonist + antagonist mid-conflict.
4. Three hook options per script, scored against these rules + novelty vs. past hooks.
5. Do **not** copy any reference title; the engine checks similarity against `video_dataset.csv`
   titles and rejects >0.6 token-overlap.
