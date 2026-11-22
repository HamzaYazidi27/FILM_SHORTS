# FILM_SHORTS — ShortsEngine

An evidence-driven, autonomous YouTube Shorts production platform.

1. **Reverse-engineering (Phase 1):** the full catalogue of the reference channel
   [@Yah1231](https://www.youtube.com/@Yah1231/shorts) (91 Shorts) was measured from public data and real
   storyboard frames. The results are written to `research/`.
2. **Production engine (Phases 2–6):** a modular pipeline driven by the measured **Channel DNA**
   (`research/channel_dna.json`). It runs topic discovery, research and fact-checking, script, storyboard,
   assets, voice, editing, subtitles, QC, export, metadata, optional YouTube upload, and an analytics
   feedback loop. It comes with a dashboard.

The engine reproduces only the **abstract** characteristics it measured: structure, pacing, caption style and
grade. Every story, line of dialogue, character, image, voice and piece of music is original.
The reference channel's videos are cuts of third-party footage (its own descriptions say so). This engine
never downloads, reuses or imitates that footage, the creator's voice, handle or branding. See
[Originality & rights](#originality--rights).

---

## What the reference channel actually does (summary of `research/`)

| Finding (evidence) | Value |
|---|---|
| Catalogue | 91 Shorts, 2026-07-08 → 2026-11-21, 60.6 K subscribers, 61.2 M total views |
| Two eras | **A** (n=50) "Tell the story of \<film\>" recaps, ~72 s, narrator + 1-word captions · **B** (n=41) moral-lesson dialogue dramas, ~175 s |
| Era B vs A | median views 330,570 vs 26,852 (12.3×); median views/day 27,289 vs 230; like rate 2.93 % vs 1.59 % |
| Era B duration | median 175 s, p10–p90 163–177 s (37/41 in 160–178 s) |
| Cadence | 1 upload/day, 36/41 between 10:00 and 12:59 UTC |
| Hook | moral as title `"<moral>"☠️🔥 #Trending #Shorts` **and** a white `POV: <moral>` bar on screen for the first ~4–6 s (41/41) |
| Opening | two-character confrontation; opening on a two-shot scores +0.43 vs −1.90 for an insert/detail opening (age-adjusted) |
| Narrative | cold open → setup → escalation → authority/turn → **reveal at ~75–90 %** → comeuppance; reaction close-up ending (33/41) |
| Editing | a new visual every ≤ 2.7 s (storyboard lower bound 22 changes/min); hard cuts, flash/dissolve at the reveal |
| Captions | 2–4-word dialogue chunks, 2 lines (2nd larger), coloured glow rotating per chunk, centred at 47 % height, on ~60 % of frames |
| Grade | luma 0.60, warmth +18, colourfulness 50: bright, warm skin, teal shadows, glow |
| Themes (age-adjusted) | parenting +1.41, kindness +0.84, greed/honesty +0.76 … entitlement −1.23 (small n, treated as priors) |

Full reports: [`channel_audit.md`](research/channel_audit.md) · [`performance_analysis.md`](research/performance_analysis.md)
· [`hook_patterns.md`](research/hook_patterns.md) · [`editing_patterns.md`](research/editing_patterns.md)
· [`visual_patterns.md`](research/visual_patterns.md) · [`audio_patterns.md`](research/audio_patterns.md)
· dataset [`video_dataset.csv`](research/video_dataset.csv) (91 rows × 50+ columns) · DNA [`channel_dna.json`](research/channel_dna.json).

**Data limitations, stated plainly.** Video and audio streams and transcripts were not retrievable from the
analysis environment because of YouTube's bot-check and PO-token gating. Visual metrics come from YouTube's
storyboard frames: real frames, sampled every ~2 s. Cut rates are therefore lower bounds, and audio conclusions
are inferences marked low-confidence. Comments were collected for 1 video. Retention data is private to the
channel owner.

---

## Architecture

```
research/tools/          collect_channel.py → analyze_frames.py → build_dataset.py   (Phase 1, reproducible)
research/channel_dna.json ─────────────────────────────────────────────────────────┐
                                                                                     ▼
shortsengine/
  dna.py            typed loader: beats, durations, pacing, captions, grade, title pattern, theme/setting priors
  pipeline/
    orchestrator.py STAGES = topic → research → script → storyboard → assets → voice → edit → qc → metadata → publish
                    persistent per-stage outputs (data/jobs/<id>/<stage>.json), retries with backoff, send-back loops,
                    budget pause, duplicate prevention, crash recovery, scheduler
    worker.py       long-running queue/scheduler/analytics loop (SIGTERM-safe)
  modules/
    topics.py       M1 discovery + scoring (relevance, novelty, DNA/learned audience evidence, curiosity,
                    Wikipedia-checked reliability, visual potential, overuse penalty; trend hook from Wikipedia most-read)
    research.py     M2 fact-checking: claims need VERBATIM quotes found in fetched pages + no unsupported numbers;
                    speculation & contradictions rejected; permanent revision links stored
    script.py       M3 staged generation (plan + one call per DNA beat), 3 scored hook options, claim-linked facts,
                    originality checks, checkpointing
    storyboard.py   M4 shots from DNA pacing/coverage grammar, asset dedup (≤20 images for ~3 min), prompts
    assets.py       M5 generation/retrieval + rights registry (source, license, date, restrictions, scene, sha256)
    voice.py        M6 per-character synthetic voices, emotion delivery, lexicon, pauses, speed, duration fitting
    music.py        original procedural music bed + SFX (numpy)
    editor.py       M7 grade → sub-pixel camera motion → captions/title bar/fact card → parallel H.264 segments →
                    concat → ducked mix → two-pass loudnorm → AAC MP4 + SRT + thumbnail
    qc.py           M8 27 technical/creative/originality checks on the actual MP4, score, reasons, send-back stage
    metadata.py     title/description/tags with sources and AI/fiction disclosure
    publish.py      YouTube Data API v3 OAuth upload (private by default, scheduling, synthetic-media flag)
    analytics.py    stats + retention → age-adjusted, shrunk, capped strategy updates; exploration suggestions
  providers/        llm.py (Claude / OpenAI-compatible e.g. Ollama / Pollinations) · images.py (Pollinations /
                    OpenAI / procedural) · tts.py (Edge / ElevenLabs / OpenAI / espeak) · wikipedia.py
  web/              FastAPI dashboard (15 sections) + vanilla JS UI
```

Data lives in `data/` (or `$SHORTS_DATA_DIR`): `settings.json`, the `shorts.db` SQLite database (migrated
automatically), `jobs/<id>/…` and `logs/engine.jsonl`.

## Installation

Requirements: Python ≥ 3.11, FFmpeg 6+ (with libx264).

**macOS (Apple Silicon)**
```bash
brew install ffmpeg python@3.12
```
```bash
python3 -m venv .venv && source .venv/bin/activate
```
```bash
pip install -e ".[dev]"
```
```bash
cp .env.example .env   # add ANTHROPIC_API_KEY (and optional keys)
```
```bash
python -m shortsengine init
```

**Linux**: `apt-get install ffmpeg`, then use the same `pip` and `.env` steps.

**Docker**
```bash
cp .env.example .env && docker compose up --build
```
The dashboard is served at http://127.0.0.1:8000, and data persists in `./data`.

Local models on a Mac: run [Ollama](https://ollama.com), then set *API & Providers → LLM* to
`openai_compatible` with base URL `http://localhost:11434/v1` and model e.g. `llama3.1`.

## Usage

```bash
python -m shortsengine serve            # dashboard + background worker on http://127.0.0.1:8000
```
```bash
python -m shortsengine discover --n 8   # discover & score topics
```
```bash
python -m shortsengine run              # produce one Short end-to-end (no upload unless enabled)
```
```bash
python -m shortsengine run --until script   # stop after a stage; continue later with `resume <job>`
```
```bash
python -m shortsengine retry 3 --from-stage voice   # re-run a job from any stage, reusing earlier outputs
```
```bash
python -m shortsengine worker           # headless scheduler/queue worker
```

Automation is **paused by default**. Start it from *Overview* or *Automation Settings*, where you can also set
videos/day, max/day, run times (UTC), daily and per-video budgets, QC loop limits, and publishing settings.

### Providers

| Kind | Options | Notes |
|---|---|---|
| LLM | `anthropic` (default, `claude-opus-5-5`, structured JSON output, server-side refusal fallback), `openai_compatible` (Ollama/LM Studio/vLLM/gateways), `pollinations` (keyless dev fallback, 1 reply / 45 s) | costs tracked per token |
| Images | `pollinations` (FLUX-class), `openai` (gpt-image-1), `procedural` (offline original fallback) | license stored per asset |
| TTS | `edge` (neural voices, word timings), `elevenlabs` (timestamps), `openai`, `espeak` | stock voices only — no cloning |
| Research | Wikipedia/Wikimedia API | permanent revision links |

### YouTube publishing (optional)

1. In Google Cloud Console, enable **YouTube Data API v3** and **YouTube Analytics API**. Create an OAuth client
   of type *Desktop app* and download `client_secret.json`.
2. Set `YOUTUBE_CLIENT_SECRETS=/path/to/client_secret.json` in `.env`.
3. Authorise once with `python -m shortsengine youtube-auth`. The token is stored in `data/secrets/`.
4. Upload from *Publishing*, or enable `automation.auto_publish`.

Uploads are **private** by default. A `public` privacy setting with auto-publish schedules the video for
`publish_hour_utc` (the DNA window is 10–12 UTC) using `publishAt`. `containsSyntheticMedia` is always set.

### Analytics & learning

The worker refreshes statistics every `analytics_refresh_hours`. Average view duration and percentage plus the
retention curve come from the Analytics API. Performance is age-adjusted using the same method as the Phase 1
research. Feature effects (theme, setting, hook grammar, duration, pace, opening) are shrunk with
`n/(n+5)`, capped at ±0.6 and need n ≥ 2, so one video never swings the strategy. Retention drops are mapped
onto narrative beats. The dashboard reports these as associations, not causes, and suggests under-tested themes.

## Quality control

Each render is checked on the actual MP4:
- **Technical:** resolution, 9:16, fps, H.264/AAC/yuv420p, audio present, duration in the DNA range and ≤ 180 s,
  no black segments (`blackdetect`), clean decode, no clipping (peak and true-peak), loudness, assets present,
  captions inside the safe area.
- **Creative:** two-shot opening, POV hook bar, first words ≤ 0.6 s, speaker-on-screen sync, caption/audio
  sync, beat order, no dead air, pacing vs. the DNA shot length, reveal position, reaction-close-up ending,
  a sourced fact card, all facts cite verified claims.
- **Originality and rights:** licenses, no third-party media, no duplicate script, no copied title,
  structure variety.

Each failure explains itself and sends the job back to the responsible stage, up to `max_qc_loops`.

## Tests

```bash
python -m pytest -q
```

The suite covers DNA loading, claim verification (hallucinated numbers and quotes are rejected), caption
chunking, word alignment, budgets, migrations, QC media analysis on synthetic black/clipped video, the
publish request body and a fake-client upload, and analytics shrinkage. Integration tests run the **full
pipeline** with deterministic providers and check the MP4 with ffprobe, QC pass, resume after a crash
without redoing stages, transient-failure retry, research-rejection send-back, budget pause, the scheduler,
and the dashboard API.

## End-to-end results

See [`E2E_RESULTS.md`](E2E_RESULTS.md) for the real-provider run (live LLM, Wikipedia fact-checking, generated
images, neural TTS, render, QC) and its measured output.

## Originality & rights

- Only abstract patterns are reproduced. Titles are blocked if they are too similar to any reference title
  (Jaccard > 0.6), and scripts are blocked if their 4-gram overlap with previous scripts exceeds 0.25.
- No footage, frames, audio or text from the reference channel or the works it uses ever enter the pipeline.
  Storyboard frames were used for measurement only. They are not committed and are regenerable with
  `research/tools/collect_channel.py`.
- Every video carries a fiction and AI disclosure, and every factual statement links to its source.
- No voice cloning, no imitation of the reference creator's handle or watermark (`brand_handle` is your
  own and optional), and no cookie-based or unauthorised account access.
