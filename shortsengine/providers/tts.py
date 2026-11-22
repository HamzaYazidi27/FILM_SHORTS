"""Text-to-speech adapters with a common interface returning audio + word timings.

* KokoroTTS      - Kokoro-82M via ONNX, fully local/offline (Apache-2.0), 50+ stock voices (default)
* EdgeTTS        - Microsoft neural voices via edge-tts, word-boundary timings (no key)
* ElevenLabsTTS  - ElevenLabs /with-timestamps (needs ELEVENLABS_API_KEY)
* OpenAITTS      - OpenAI speech API (needs OPENAI_API_KEY); timings estimated
* EspeakTTS      - local espeak-ng (offline); timings estimated

Voices are stock synthetic voices. Cloning or imitating a real person's voice is not supported.
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from .. import costs, logs
from ..config import secret

log = logs.get("tts")
WORD_RE = re.compile(r"[\w'’-]+|[^\w\s]", re.UNICODE)


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class TTSResult:
    wav_path: Path
    duration: float
    words: list[Word]
    provider: str
    voice: str


class TTSError(RuntimeError):
    pass


def to_wav(src: Path, dst: Path, sr: int = 48000) -> float:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)],
                   check=True)
    return probe_duration(dst)


def probe_duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def apply_pronunciations(text: str, lexicon: dict[str, str]) -> str:
    for k, v in lexicon.items():
        text = re.sub(rf"\b{re.escape(k)}\b", v, text, flags=re.IGNORECASE)
    return text


def display_words(text: str) -> list[str]:
    return [t for t in text.split() if t.strip()]


def align_words(display: list[str], spoken: list[Word]) -> list[Word]:
    """Map timings from spoken tokens (possibly altered by the lexicon) onto display words."""
    if not spoken:
        return []
    norm = lambda s: re.sub(r"[^\w]", "", s.lower())
    a = [norm(w) for w in display]
    b = [norm(w.text) for w in spoken]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out: list[Word | None] = [None] * len(display)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                s = spoken[j1 + k]
                out[i1 + k] = Word(display[i1 + k], s.start, s.end)
        elif j2 > j1:
            st, en = spoken[j1].start, spoken[j2 - 1].end
            n = i2 - i1
            for k in range(n):
                out[i1 + k] = Word(display[i1 + k], st + (en - st) * k / n, st + (en - st) * (k + 1) / n)
    # fill gaps by interpolation
    for i, w in enumerate(out):
        if w is None:
            prev = next((out[j] for j in range(i - 1, -1, -1) if out[j]), None)
            nxt = next((out[j] for j in range(i + 1, len(out)) if out[j]), None)
            st = prev.end if prev else (nxt.start if nxt else 0.0)
            en = nxt.start if nxt else st + 0.25
            out[i] = Word(display[i], st, max(st + 0.05, en))
    return [w for w in out if w]


def estimate_words(text: str, duration: float, lead: float = 0.05) -> list[Word]:
    words = display_words(text)
    weights = [max(2, len(re.sub(r"\W", "", w))) + (3 if re.search(r"[,.!?;:]$", w) else 0) for w in words]
    total = sum(weights) or 1
    t, out = lead, []
    span = max(0.1, duration - lead - 0.05)
    for w, wt in zip(words, weights):
        d = span * wt / total
        out.append(Word(w, t, t + d * 0.92))
        t += d
    return out


class EdgeTTS:
    name = "edge"

    def synth(self, text: str, voice: str, out_dir: Path, stem: str, rate_pct: int = 0, pitch_hz: int = 0,
              lexicon: dict | None = None, job_id: int | None = None) -> TTSResult:
        import edge_tts
        spoken_text = apply_pronunciations(text, lexicon or {})
        mp3 = out_dir / f"{stem}.mp3"
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")

        async def run() -> list[Word]:
            comm = edge_tts.Communicate(spoken_text, voice, rate=f"{rate_pct:+d}%", pitch=f"{pitch_hz:+d}Hz",
                                        boundary="WordBoundary", proxy=proxy)
            words: list[Word] = []
            with mp3.open("wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        st = chunk["offset"] / 1e7
                        words.append(Word(chunk["text"], st, st + chunk["duration"] / 1e7))
            return words

        last = None
        for attempt in range(4):
            try:
                spoken = asyncio.run(run())
                if mp3.stat().st_size > 0:
                    break
                last = "empty audio"
            except Exception as e:  # network / service errors
                last = e
            import time
            time.sleep(3 * (attempt + 1))
        else:
            raise TTSError(f"edge-tts failed for voice {voice}: {last}")
        wav = out_dir / f"{stem}.wav"
        dur = to_wav(mp3, wav)
        mp3.unlink(missing_ok=True)
        words = align_words(display_words(text), spoken) or estimate_words(text, dur)
        costs.record("edge", "tts", len(text), "chars", 0.0, job_id, "voice", {"voice": voice})
        return TTSResult(wav, dur, words, self.name, voice)


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(self):
        self.key = secret("ELEVENLABS_API_KEY")
        if not self.key:
            raise TTSError("ELEVENLABS_API_KEY not configured")

    def synth(self, text, voice, out_dir, stem, rate_pct=0, pitch_hz=0, lexicon=None, job_id=None) -> TTSResult:
        spoken_text = apply_pronunciations(text, lexicon or {})
        r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps",
                       headers={"xi-api-key": self.key},
                       json={"text": spoken_text, "model_id": "eleven_multilingual_v2",
                             "voice_settings": {"stability": 0.45, "similarity_boost": 0.75, "speed": 1 + rate_pct / 100}},
                       timeout=120)
        if r.status_code != 200:
            raise TTSError(f"elevenlabs HTTP {r.status_code}: {r.text[:200]}")
        d = r.json()
        mp3 = out_dir / f"{stem}.mp3"
        mp3.write_bytes(base64.b64decode(d["audio_base64"]))
        wav = out_dir / f"{stem}.wav"
        dur = to_wav(mp3, wav)
        al = d.get("alignment") or {}
        chars, starts, ends = al.get("characters", []), al.get("character_start_times_seconds", []), al.get("character_end_times_seconds", [])
        spoken, cur, cs = [], "", None
        for c, s, e in zip(chars, starts, ends):
            if c.isspace():
                if cur:
                    spoken.append(Word(cur, cs, prev_e))
                cur, cs = "", None
            else:
                cur += c
                cs = s if cs is None else cs
                prev_e = e
        if cur:
            spoken.append(Word(cur, cs, prev_e))
        costs.record("elevenlabs", "tts", len(text), "chars", len(text) / 1000 * costs.TTS_PRICES_PER_1K_CHARS["elevenlabs"], job_id, "voice")
        return TTSResult(wav, dur, align_words(display_words(text), spoken) or estimate_words(text, dur), self.name, voice)


class OpenAITTS:
    name = "openai"

    def __init__(self):
        self.key = secret("OPENAI_API_KEY")
        if not self.key:
            raise TTSError("OPENAI_API_KEY not configured")

    def synth(self, text, voice, out_dir, stem, rate_pct=0, pitch_hz=0, lexicon=None, job_id=None) -> TTSResult:
        r = httpx.post("https://api.openai.com/v1/audio/speech", headers={"Authorization": f"Bearer {self.key}"},
                       json={"model": "gpt-4o-mini-tts", "voice": voice, "input": apply_pronunciations(text, lexicon or {}),
                             "speed": 1 + rate_pct / 100, "response_format": "wav"}, timeout=120)
        if r.status_code != 200:
            raise TTSError(f"openai tts HTTP {r.status_code}: {r.text[:200]}")
        raw = out_dir / f"{stem}.src.wav"
        raw.write_bytes(r.content)
        wav = out_dir / f"{stem}.wav"
        dur = to_wav(raw, wav)
        raw.unlink(missing_ok=True)
        costs.record("openai", "tts", len(text), "chars", len(text) / 1000 * costs.TTS_PRICES_PER_1K_CHARS["openai"], job_id, "voice")
        return TTSResult(wav, dur, estimate_words(text, dur), self.name, voice)


class EspeakTTS:
    name = "espeak"

    def __init__(self):
        self.bin = shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.bin:
            raise TTSError("espeak-ng not installed")

    def synth(self, text, voice, out_dir, stem, rate_pct=0, pitch_hz=0, lexicon=None, job_id=None) -> TTSResult:
        raw = out_dir / f"{stem}.src.wav"
        subprocess.run([self.bin, "-v", voice or "en-us", "-s", str(int(165 * (1 + rate_pct / 100))),
                        "-w", str(raw), apply_pronunciations(text, lexicon or {})], check=True)
        wav = out_dir / f"{stem}.wav"
        dur = to_wav(raw, wav)
        raw.unlink(missing_ok=True)
        return TTSResult(wav, dur, estimate_words(text, dur), self.name, voice)


KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def kokoro_dir() -> Path:
    from ..config import data_dir
    d = Path(os.environ.get("KOKORO_MODEL_DIR") or data_dir() / "models" / "kokoro")
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_kokoro_files(model: str = "kokoro-v1.0.int8.onnx") -> tuple[Path, Path]:
    """Download the Kokoro model + voices once (public GitHub release, no key) and reuse them offline."""
    d = kokoro_dir()
    out = []
    for name in (model, "voices-v1.0.bin"):
        f = d / name
        if not f.exists() or f.stat().st_size < 1_000_000:
            log.info("downloading %s (one-time)", name)
            tmp = f.with_suffix(".part")
            with httpx.stream("GET", KOKORO_RELEASE + name, follow_redirects=True, timeout=600) as r:
                if r.status_code != 200:
                    raise TTSError(f"could not download {name}: HTTP {r.status_code}")
                with tmp.open("wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)
            tmp.replace(f)
        out.append(f)
    return out[0], out[1]


class KokoroTTS:
    """Local neural TTS. Speed control via `rate_pct`; pitch is not supported by the model and is ignored.
    Word timings are estimated inside the trimmed speech span of each (short) line."""
    name = "kokoro"
    _engine = None
    _lock = __import__("threading").Lock()

    def __init__(self):
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError as e:
            raise TTSError("kokoro-onnx not installed: pip install 'kokoro-onnx' soundfile") from e

    def _get(self):
        with KokoroTTS._lock:
            if KokoroTTS._engine is None:
                from kokoro_onnx import Kokoro
                from ..config import load_settings
                m, v = ensure_kokoro_files(load_settings().voice.kokoro_model)
                KokoroTTS._engine = Kokoro(str(m), str(v))
            return KokoroTTS._engine

    def synth(self, text, voice, out_dir, stem, rate_pct=0, pitch_hz=0, lexicon=None, job_id=None) -> TTSResult:
        import numpy as np
        import soundfile as sf
        eng = self._get()
        lang = "en-gb" if voice.startswith("b") else "en-us"
        with KokoroTTS._lock:  # onnxruntime session is shared; serialise calls
            audio, sr = eng.create(apply_pronunciations(text, lexicon or {}), voice=voice,
                                   speed=max(0.6, min(1.6, 1 + rate_pct / 100)), lang=lang)
        raw = Path(out_dir) / f"{stem}.src.wav"
        sf.write(str(raw), audio, sr)
        wav = Path(out_dir) / f"{stem}.wav"
        dur = to_wav(raw, wav)
        raw.unlink(missing_ok=True)
        env = np.abs(audio)
        thr = max(1e-3, float(env.max()) * 0.04)
        idx = np.where(env > thr)[0]
        lead = float(idx[0]) / sr if len(idx) else 0.0
        tail = float(idx[-1]) / sr if len(idx) else dur
        words = [Word(w.text, w.start + lead, w.end + lead) for w in estimate_words(text, max(0.2, tail - lead), lead=0.0)]
        costs.record("kokoro", "tts", len(text), "chars", 0.0, job_id, "voice", {"voice": voice})
        return TTSResult(wav, dur, words, self.name, voice)


_override = None


def set_tts(t) -> None:
    global _override
    _override = t


def get_tts(kind: str):
    if _override is not None:
        return _override
    return {"kokoro": KokoroTTS, "edge": EdgeTTS, "elevenlabs": ElevenLabsTTS, "openai": OpenAITTS,
            "espeak": EspeakTTS}[kind]()
