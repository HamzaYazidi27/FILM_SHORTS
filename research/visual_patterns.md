# Visual patterns

All colour values are medians over every storyboard frame of each era
(`frame_metrics.json`; luma/saturation in 0–1, warmth = mean(R−B) in 0–255 units,
colourfulness = Hasler–Süsstrunk metric).

## V1 — Colour grade (consistent, high confidence)

| Metric | Era A | Era B |
|---|---|---|
| mean luma | 0.438 | **0.603** |
| mean saturation | 0.259 | 0.279 |
| warmth (R−B) | +10.2 | **+18.3** |
| colourfulness | 28.7 | **50.3** |
| contrast (luma std) | 48.0 | 54.3 |

Era B looks bright, warm-skinned and colourful with teal/cyan shadows and a soft glow
("HDR/clarity" look). Visible in every Era B contact sheet (`eraB_first` / `eraB_last`
reconstructions from `tools/analyze_frames.py`). Grade does not differ across tiers
(top 48.1 vs bottom 53.2 colourfulness) → a constant to reproduce, not a lever.

## V2 — Captions (consistent, high confidence for style)

| | Era A | Era B |
|---|---|---|
| frames with caption | 25.4 % | **59.6 %** |
| caption vertical centre | 77 % of height | **47 %** of height |
| words per caption | 1 | 2–4, split over 2 lines |
| style | plain white bold | uppercase bold condensed; line 2 larger; coloured outer glow; colour rotates per line (yellow, green, magenta, blue, orange) |

Example frames: `c8n65RTCZz8` ("YOU HAVE TO PAY", "FINALLY GOOD / RIDDANCE"), `4-WzLJxiPJk`
("I WILL GET MYSELF / ANOTHER BURGER", "THIS IS YOUR / WALLET").

Caption presence has the strongest (still weak) association with performance among visual
metrics (Spearman 0.195 vs. age-adjusted score).

## V3 — Title bar (consistent, high confidence)

White full-width strip, top ~7 % of the frame, black condensed bold uppercase
"POV: <moral>", 1–2 lines; present in 41/41 Era B first frames and 3/50 Era A.

## V4 — Composition

Full-frame vertical (no letterboxing in Era B), subject centred, faces in the upper-middle
third so the 47 % caption sits around chin/chest. Backgrounds are real interiors
(airplane cabin, shop floors, restaurant, venues) with shallow depth of field.

## V5 — First and last frames

* First frame: scene with ≥2 people (24/41 are two-shot or group), POV bar on top.
* Last frame: close-up of a face reacting (33/41); 4/41 fade to black. No end card, no
  subscribe graphic observed.

## V6 — Black frames

Only 1/41 Era B videos contains a dark sample mid-video; the end fades are intentional.
Engine QC rejects unintended black frames.

## Engine parameters derived

```
grade: luma≈0.60, warmth +18, colourfulness≈50, soft glow, teal shadows
captions: y=0.47, 2 lines, 1–4 words, uppercase, glow, colour rotation per caption
title_bar: top, white, black text, 0–5 s
first shot: two-shot; last shot: reaction close-up
```
