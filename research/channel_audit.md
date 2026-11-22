# Channel audit — Yah Kah (@Yah1231)

Snapshot: **2026-11-22 01:50 UTC**. All numbers come from public data collected by
`research/tools/collect_channel.py` and the derived files in `research/`. Nothing below is
guessed: where evidence is missing, it says so.

## 1. Channel intelligence

| Field | Value | Evidence |
|---|---|---|
| Channel name | Yah Kah | `raw/channel_flat.json` |
| Handle / ID | @Yah1231 / `UCdckSUJdfMDWK3pIXooYQmA` | same |
| Channel description | empty (none published) | same |
| Subscribers | 60,600 (rounded public count) | `channel_follower_count` |
| Shorts listed | **91** (all analysed — the full catalogue, not a sample) | flat playlist |
| Total views (91 Shorts) | 61,207,067 | sum of `view_count` |
| Total likes | 1,898,283 | sum of `like_count` |
| Date range | 2026-07-08 → 2026-11-21 | `timestamp` |
| Category | "People & Blogs" (91/91) | metadata |
| Language | English (captions, titles); 1 Spanish recap (`l0-zbSDzWx8`) | titles, burned-in captions |
| Publishing frequency | Era A 0.56/day; Era B **1.02/day** (41 uploads in 41 days) | `performance_summary.json` |
| Publish time | Era B: 36/41 uploads between 10:00–12:59 UTC | `publish_hour_utc` |

Because the channel has only 91 Shorts, the dataset is the **whole channel**, which covers the
requested "most viewed / latest / average / low performers" by construction.

## 2. Two distinct content eras (the key finding)

| | Era A — "movie recap" | Era B — "moral reversal drama" |
|---|---|---|
| Dates | 2026-07-08 → 2026-10-05 | 2026-10-12 → 2026-11-21 |
| Videos | 50 | 41 |
| Title pattern | `Tell the story of "<film>" #movie #foryou #edit #funny` | `"<moral aphorism>"☠️🔥 #Trending #Shorts` |
| Duration median (p10–p90) | 72.5 s (61–99) | **175 s (163–177)** |
| Median views | 26,852 | **330,570** (12.3×) |
| Median views/day | 230 | **27,289** (119×) |
| Median like rate | 1.59 % | **2.93 %** |
| Median comment rate | 0.0037 % | 0.0133 % |
| Median views / subscriber | 0.44 | 5.46 |
| Voice | single narrator retelling a film | character dialogue, no narrator |
| Captions | one white word at ~77 % height | 2–4 word dialogue chunks, coloured glow, ~47 % height |
| Opening | mid-action film frame, no title bar (6 %) | "POV: <moral>" white title bar in **41/41**, on screen ~4–6 s |

Confidence that Era B outperforms Era A: **high** (no overlap in quartiles: Era A Q3 = 31,513
views < Era B Q1 = 205,855). Causality is *not* established — the switch also coincided with
a later period and more channel subscribers — but the channel itself abandoned Era A, which is
consistent with the numbers.

## 3. Content positioning (evidence)

* **What the videos are.** Era B videos are 2½–3 minute cuts of *scripted moral-lesson skits*
  (staged confrontations in airplanes, shops, restaurants, venues) re-edited with stylised
  captions, a colour grade and a music bed. The first four Era B titles name the TV series the
  footage came from (Tacoma FD, Kim's Convenience, Grey's Anatomy, Law & Order SVU).
* **Own statement.** Every Era B description says: *"This video contains copyrighted content,
  including images, video, and music owned by their respective creators. We do not claim
  ownership."* and credits "Software Used: After Effects 2025" and "Track: I'm god".
* **Audience perception** (60 comments on the top video `c8n65RTCZz8`): comparisons to
  "Dhar Mann skits", requests for the "movie name", moral reactions ("served him right",
  "KARMA always happens", "never judge a book by its cover"), and some "Those AI videos…"
  remarks. Low confidence (one video's comments; further comment collection was not performed —
  see Limitations).

**Positioning hypothesis (medium confidence):** an English-language "moral karma" channel — a
social-justice confrontation where the person who is judged, humiliated or accused turns out to
hold the power (identity reveal, evidence reveal, authority arrives), told in ~3 minutes and
titled with the lesson.

**Target audience hypotheses (low–medium confidence):** broad general-audience Shorts viewers
who engage with justice/karma narratives (comments are moral reactions rather than topical
discussion). No evidence about age or gender is available publicly; **no geographic hypothesis
is made** — the only signals are English language and the 10:00–12:59 UTC upload window, which
is insufficient.

## 4. What drives performance inside Era B (see `performance_analysis.md`)

Raw views are age-biased (Spearman ρ = −0.57 between views/day and age), so all comparisons use
an **age-adjusted score** = residual of log(views) regressed on log(age) within the era.

* Top: `c8n65RTCZz8` "Being young doesn't mean you lack talent" — 25.6 M views (+4.16).
* Themes above era median: parenting (+1.41, n=2), kindness/goodwill (+0.84, n=6),
  greed vs honesty (+0.76, n=2). Below: entitlement/boundaries (−1.23, n=5), slander (−0.64).
* Opening shot: two-character medium shot +0.43 (n=14) vs. detail/insert opening −1.90 (n=6).
* Setting: airplane +1.02 (n=7) is the most common and best-performing setting.
* Visual metrics (grade, cut rate) are nearly identical across performance tiers → they are a
  *format constant*, not a differentiator (ρ ≤ 0.2 in absolute value).

## 5. Limitations (exactly what was not available)

1. **Video/audio streams**: YouTube's web client returned "Sign in to confirm you're not a bot"
   from this environment, and stream URLs require a PO token. No video or audio file was
   downloaded. Visual analysis uses YouTube storyboards (real frames, 180 px cells, one frame per
   ~1.96 s in Era B), so cut counts are lower bounds and camera motion inside a shot is not
   measurable.
2. **Transcripts**: auto-caption tracks are listed for 41 videos but the timedtext endpoint
   returned empty bodies. Dialogue evidence comes from captions burned into the frames.
3. **Audio**: speaking speed, pauses, voice/music balance and SFX could not be measured. Audio
   conclusions in `audio_patterns.md` are inferences and are marked low confidence.
4. **Comments**: collected for one video (60 comments). A broader comment collection was
   blocked in this session; `collect_channel.py --comments N` will collect them where allowed.
5. **Retention / average view duration** are private to the channel owner and unavailable.

## 6. Files

| File | Content |
|---|---|
| `video_dataset.csv` / `.json` | 91 rows × 50+ columns: metadata, engagement ratios, age-adjusted score, tier, frame metrics, annotations |
| `frame_metrics.json` | per-video computed visual metrics from storyboards |
| `annotations.json` | manual per-video labels (setting, opening shot, ending shot) for Era B, made from frames |
| `performance_summary.json` | aggregates by era, theme, title grammar, duration, setting, shot type, tier; correlations |
| `channel_dna.json` | machine-readable creative DNA consumed by the production engine |
| `hook_patterns.md`, `editing_patterns.md`, `visual_patterns.md`, `audio_patterns.md`, `performance_analysis.md` | detailed findings |
| `tools/` | reproducible collection and analysis scripts |
