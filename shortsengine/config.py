"""Configuration: secrets from the environment (.env), editable settings in data/settings.json.

Secrets (API keys, OAuth paths) are never stored in settings.json or source code; the
dashboard can only see whether a secret is present.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def data_dir() -> Path:
    d = Path(os.environ.get("SHORTS_DATA_DIR", ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


class VoiceProfile(BaseModel):
    """A synthetic voice slot. Never a clone of a real person."""
    provider_voice: str
    rate_pct: int = 0          # -50..+50
    pitch_hz: int = 0          # -50..+50


class VoiceSettings(BaseModel):
    provider: Literal["kokoro", "edge", "elevenlabs", "openai", "espeak"] = "kokoro"   # kokoro = local, no key
    language: str = "en-US"
    words_per_minute: int = 150
    line_gap_s: float = 0.22
    beat_gap_s: float = 0.45
    pre_reveal_pause_s: float = 0.7
    pronunciations: dict[str, str] = Field(default_factory=dict)
    # role -> voice pools; the engine assigns distinct voices per character deterministically
    male_voices: list[str] = Field(default_factory=lambda: [
        "en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-EricNeural", "en-US-RogerNeural", "en-US-SteffanNeural"])
    female_voices: list[str] = Field(default_factory=lambda: [
        "en-US-JennyNeural", "en-US-AriaNeural", "en-US-MichelleNeural", "en-US-AnaNeural", "en-US-EmmaNeural"])
    # Kokoro (local, Apache-2.0) voice pools: a* = American, b* = British
    kokoro_male_voices: list[str] = Field(default_factory=lambda: [
        "am_michael", "am_adam", "am_eric", "bm_george", "am_liam", "bm_lewis", "am_onyx"])
    kokoro_female_voices: list[str] = Field(default_factory=lambda: [
        "af_heart", "af_bella", "af_nicole", "bf_emma", "af_sarah", "bf_isabella", "af_nova"])
    kokoro_model: str = "kokoro-v1.0.int8.onnx"     # or kokoro-v1.0.onnx (fp32, slightly better)
    loudness_lufs: float = -14.0
    true_peak_db: float = -1.0


class VideoSettings(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 19
    preset: str = "medium"
    audio_bitrate: str = "192k"
    brand_handle: str = ""                 # optional own watermark text; empty = none
    music_enabled: bool = True
    music_duck_db: float = -16.0
    sfx_enabled: bool = True
    motion_strength: float = 1.0           # 0 disables simulated camera motion
    render_workers: int = 4
    duration_override_s: float | None = None   # None = use Channel DNA target

    @field_validator("width", "height")
    @classmethod
    def _even(cls, v: int) -> int:
        if v % 2:
            raise ValueError("dimensions must be even for yuv420p")
        return v


class ProviderSettings(BaseModel):
    # Defaults are fully local (no API keys): Ollama + FLUX.1-schnell via mflux + Kokoro
    llm: Literal["ollama", "anthropic", "openai_compatible", "pollinations"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:14b"          # fits 32 GB Apple Silicon; alternatives: gemma3:12b, mistral-small3.1:24b
    ollama_num_ctx: int = 16384
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    openai_compatible_base_url: str = "http://localhost:11434/v1"   # e.g. Ollama on macOS
    openai_compatible_model: str = "llama3.1"
    llm_fallback: Literal["none", "ollama", "pollinations", "openai_compatible"] = "none"
    image: Literal["mflux", "a1111", "pollinations", "openai", "procedural"] = "mflux"
    mflux_model: str = "schnell"               # FLUX.1-schnell, Apache-2.0
    mflux_model_path: str = ""                 # optional pre-quantized local model dir (saves the first download)
    mflux_quantize: int = 4
    mflux_steps: int = 4
    a1111_base_url: str = "http://127.0.0.1:7860"   # Draw Things / Forge / SD.Next HTTP API
    a1111_steps: int = 8
    local_image_license: str = "AI-generated (local model; see the model's license)"
    image_fallback: Literal["none", "procedural"] = "procedural"
    image_model: str = "flux"
    research: Literal["wikipedia"] = "wikipedia"


class AutomationSettings(BaseModel):
    enabled: bool = False                  # start/pause automation
    videos_per_day: int = 1
    max_videos_per_day: int = 3
    run_times_utc: list[str] = Field(default_factory=lambda: ["09:30"])
    daily_budget_usd: float = 5.0
    per_video_budget_usd: float = 2.5
    max_stage_attempts: int = 3
    max_qc_loops: int = 2
    min_topic_score: float = 0.55
    require_fact_anchor: bool = True
    auto_publish: bool = False             # never on by default
    publish_privacy: Literal["private", "unlisted", "public"] = "private"
    publish_hour_utc: int = 11             # DNA: reference uploads cluster 10-12 UTC
    analytics_refresh_hours: int = 12


class Settings(BaseModel):
    dna_path: str = "research/channel_dna.json"
    format: str | None = None              # None = DNA active_format
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    video: VideoSettings = Field(default_factory=VideoSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)


SECRET_ENV = {
    "ANTHROPIC_API_KEY": "Claude API (LLM)",
    "OPENAI_API_KEY": "OpenAI images / TTS (optional)",
    "OPENAI_COMPATIBLE_API_KEY": "OpenAI-compatible LLM endpoint (optional)",
    "ELEVENLABS_API_KEY": "ElevenLabs TTS (optional)",
    "POLLINATIONS_API_KEY": "Pollinations key (optional; removes free-tier limits/watermark)",
    "YOUTUBE_CLIENT_SECRETS": "Path to Google OAuth client_secret.json (publishing)",
}

_lock = threading.Lock()


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> Settings:
    p = settings_path()
    if p.exists():
        try:
            return Settings.model_validate_json(p.read_text())
        except ValidationError as e:  # fail loudly: invalid config must not silently reset
            raise RuntimeError(f"Invalid settings in {p}: {e}") from e
    s = Settings()
    save_settings(s)
    return s


def save_settings(s: Settings) -> None:
    with _lock:
        p = settings_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(s.model_dump_json(indent=2))
        tmp.replace(p)


def update_settings(patch: dict) -> Settings:
    """Deep-merge a partial dict into current settings and validate."""
    cur = json.loads(load_settings().model_dump_json())

    def merge(a: dict, b: dict) -> dict:
        for k, v in b.items():
            a[k] = merge(a.get(k, {}), v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
        return a

    s = Settings.model_validate(merge(cur, patch))
    save_settings(s)
    return s


def secret(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def secrets_status() -> dict[str, dict]:
    return {k: {"purpose": v, "configured": bool(secret(k))} for k, v in SECRET_ENV.items()}
