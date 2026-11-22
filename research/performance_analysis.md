# Performance analysis

Dataset: all 91 Shorts (`video_dataset.csv`), snapshot 2026-11-22 01:50 UTC, 60.6 K subscribers.
Regenerate with `python research/tools/build_dataset.py`.

## Method

* **Views/day** = views ÷ age in days. Strongly biased toward recent uploads in this channel
  (Spearman ρ = −0.57 with age inside Era B: Shorts accumulate most views in the first days,
  then plateau).
* **Age-adjusted score** (primary metric) = residual of `log(views)` from a linear fit on
  `log(age_days)` inside each era. 0 = typical for its age; +1 ≈ 2.7× more views than
  expected; −1 ≈ 2.7× fewer.
* **Tiers** = quintiles of the age-adjusted score inside each era (top/high/mid/low/bottom).
* Like rate = likes/views; comment rate = comments/views; views/subscriber = views ÷ 60,600.
* Correlations are Spearman, descriptive only. **Correlation ≠ causation**; groups are small.

## Distribution

| | Era A (n=50) | Era B (n=41) |
|---|---|---|
| views Q1 / median / Q3 | 9,274 / 26,852 / 31,513 | 205,855 / 330,570 / 1,147,194 |
| min / max | 809 / 927,864 | 29,594 / 25,644,656 |
| median like rate | 1.59 % | 2.93 % |
| median comment rate | 0.0037 % | 0.0133 % |
| median views/subscriber | 0.44 | 5.46 |

Era B is heavy-tailed: the top video (25.6 M) holds 42 % of all channel views.

## Best and worst (Era B, age-adjusted)

| Video | Views | Age (d) | Score | Theme | Setting | Opening |
|---|---|---|---|---|---|---|
| c8n65RTCZz8 "Being young doesn't mean you lack talent" | 25,644,656 | 29.6 | +4.16 | prejudice | airplane | medium single |
| 4-WzLJxiPJk "He taught his child the most important lesson" | 3,282,116 | 31.6 | +2.11 | parenting | restaurant | two-shot |
| r2OMChBFsOk "She thought he was blind, but he saw everything" | 3,334,719 | 14.7 | +2.01 | prejudice | public space | two-shot |
| JzG-CDmGaY8 "Greed makes you lose everything…" | 3,049,508 | 12.6 | +1.89 | greed/honesty | street | medium single |
| oVpugxG5DSs "A badge doesn't let you do whatever you want" | 2,098,175 | 33.6 | +1.68 | authority | airplane | two-shot |
| … | | | | | | |
| I26t3qPkjO4 "Arrogance can cost you everything" | 51,547 | 23.6 | −2.09 | authority | retail | insert |
| PYIRZoxt3uE "Love often gives people more motivation" | 32,392 | 25.6 | −2.54 | motivation | home | insert |
| TTqUFtjdBLE "Don't blindly give in to passengers' unreasonable demands" | 29,594 | 36.6 | −2.57 | entitlement | airplane | insert |

## Topic clusters (Era B)

| Theme | n | age-adj median |
|---|---|---|
| parenting/family | 2 | +1.41 |
| kindness/goodwill | 6 | +0.84 |
| greed/honesty | 2 | +0.76 |
| respect/authority | 6 | 0.00 |
| character/motivation | 3 | −0.13 |
| prejudice/appearance | 10 | −0.18 |
| bullying/disability | 2 | −0.30 |
| quick thinking | 2 | −0.54 |
| slander | 2 | −0.64 |
| entitlement/boundaries | 5 | −1.23 |

## Hook categories

See `hook_patterns.md`: opening on a two-shot (+0.43, n=14) vs insert (−1.90, n=6) is the
largest within-era difference found. Title grammar effects are small and overlapping.

## Duration

| Bucket | Era | n | age-adj median |
|---|---|---|---|
| 60–74 s | A | 28 | +0.41 |
| 75–89 s | A | 15 | −0.28 |
| 90–119 s | A | 7 | +0.31 |
| 120–159 s | B | 3 | −0.20 |
| 160–178 s | B | 37 | −0.27 (bulk) |

Within each era duration is nearly constant, so no within-era duration effect can be inferred
(ρ = 0.12). Across eras the long (~175 s) dialogue dramas vastly outperform the ~72 s recaps,
but format, theme and time are confounded. **Engine default: 172 s target (155–178 s), from
the measured Era B distribution.**

## Visual metrics vs performance (Era B, Spearman vs age-adjusted score)

| Metric | ρ |
|---|---|
| caption frame ratio | +0.195 |
| duration | +0.119 |
| publish hour | +0.065 |
| cut rate | −0.020 |
| saturation | −0.028 |
| warmth | −0.029 |
| luma | −0.088 |
| colourfulness | −0.162 |
| like rate | −0.212 |

None are strong. The visual style is a constant of the format; story/opening choices vary
more with outcome.

## Recommendations encoded in `channel_dna.json`

1. Use the moral-reversal-drama format (Era B), ~172 s.
2. Open on a two-character confrontation; never on an insert.
3. Prefer kindness, parenting, greed-vs-honesty and authority-abuse themes; keep prejudice
   stories but with a strong visible status hierarchy (airplane/retail settings).
4. Treat grade/cut-rate as fixed style constants.
5. Re-evaluate with the system's own analytics (Phase 5) — these are priors, not laws.
