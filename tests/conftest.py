"""Test fixtures: isolated data dir per test, deterministic LLM/TTS doubles and an offline
Wikipedia stub, so the full pipeline can run hermetically (network tests are marked)."""
from __future__ import annotations

import json
import math
import re
import wave
from pathlib import Path

import numpy as np
import pytest

from shortsengine import db
from shortsengine.providers import llm as llm_mod
from shortsengine.providers import tts as tts_mod
from shortsengine.providers import wikipedia

FACT_TEXT = ("The Air Carrier Access Act is a United States law that prohibits commercial airlines from "
             "discriminating against passengers with disabilities. It was enacted in 1986.\n\n"
             "Airlines must provide assistance with boarding and deplaning when requested.")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTS_DATA_DIR", str(tmp_path / "data"))
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY", "YOUTUBE_CLIENT_SECRETS"):
        monkeypatch.delenv(k, raising=False)
    db.reset_connection()
    from shortsengine import dna
    dna._load.cache_clear()
    yield tmp_path
    llm_mod.set_llm(None)
    tts_mod.set_tts(None)
    db.reset_connection()


class FakeLLM(llm_mod.BaseLLM):
    """Deterministic stand-in that answers each pipeline prompt type with schema-valid JSON."""
    name = "fake"
    model = "fake"

    def __init__(self):
        self.calls = []

    def _complete(self, system, user, schema, **kw):
        props = schema.get("properties", {})
        self.calls.append(list(props))
        if "candidates" in props:
            settings = ["airplane", "retail_store", "street", "restaurant", "event_venue", "hotel_lobby"]
            themes = ["kindness_goodwill", "greed_honesty", "parenting_family", "respect_authority",
                      "prejudice_appearance", "slander_false_accusation"]
            return json.dumps({"candidates": [{
                "moral": f"Quiet dignity outlasts loud arrogance number {i}", "theme": themes[i], "setting": settings[i],
                "premise": f"A traveller {i} is mocked for an old coat; the mocker learns who they are.",
                "reveal": "she owns the company", "fact_angle": "The Air Carrier Access Act prohibits discrimination",
                "fact_search_query": "Air Carrier Access Act", "trend_link": "", "curiosity": 0.8,
                "visual_potential": 0.9} for i in range(6)]})
        if "queries" in props:
            return json.dumps({"queries": ["Air Carrier Access Act"]})
        if "claims" in props:
            return json.dumps({"claims": [
                {"claim": "The Air Carrier Access Act prohibits commercial airlines from discriminating against passengers with disabilities.",
                 "quote": "The Air Carrier Access Act is a United States law that prohibits commercial airlines from discriminating against passengers with disabilities.",
                 "page_title": "Air Carrier Access Act"},
                {"claim": "It was enacted in 1999.", "quote": "It was enacted in 1986.", "page_title": "Air Carrier Access Act"}],
                "contradictions": []})
        if "beat_plan" in props:
            return json.dumps({
                "hooks": [{"moral": "Kindness is the loudest form of power", "grammar": "general_maxim", "rationale": "r"},
                          {"moral": "Who really owns the room?", "grammar": "question", "rationale": "r"},
                          {"moral": "She mocked the quiet passenger", "grammar": "narrative_teaser", "rationale": "r"}],
                "setting": {"id": "airplane", "visual": "a bright modern airplane cabin, first class seats"},
                "characters": [
                    {"id": "maya", "name": "Maya", "gender": "female", "age": 34, "role": "protagonist",
                     "look": "woman in her thirties, short black hair, grey wool coat", "voice_style": "calm"},
                    {"id": "derek", "name": "Derek", "gender": "male", "age": 50, "role": "antagonist",
                     "look": "man in his fifties, slicked silver hair, navy suit", "voice_style": "loud"},
                    {"id": "lena", "name": "Lena", "gender": "female", "age": 28, "role": "authority",
                     "look": "flight attendant, red scarf, neat bun", "voice_style": "professional"}],
                "beat_plan": [{"beat": b, "summary": f"{b} happens"} for b in
                              ["cold_open_conflict", "setup", "escalation", "authority_or_turn", "reveal", "comeuppance_and_moral"]],
                "final_reaction": "derek", "fact_card": {"claim_id": "C1", "text": "US law bars airlines from discriminating against passengers with disabilities."}})
        if list(props) == ["lines"]:
            words = int(re.search(r"about (\d+) spoken words", user).group(1))
            beat = re.search(r"ONLY the '([a-z_]+)' beat", user).group(1)
            vocab = ("you really think you belong in this seat with that coat please move before I call "
                     "someone who matters here today because people like you never").split()
            lines, n, i = [], 0, 0
            speakers = ["derek", "maya", "lena"]
            while n < words:
                k = min(9, words - n) if words - n > 3 else words - n
                k = max(k, 3)
                text = " ".join(vocab[(i * 3 + j) % len(vocab)] for j in range(k)).capitalize() + "."
                sp = speakers[i % 3] if beat in ("authority_or_turn", "reveal") else speakers[i % 2]
                lines.append({"beat": beat, "speaker": sp, "addressee": "maya" if sp != "maya" else "derek",
                              "text": text, "emotion": ["angry", "firm", "neutral"][i % 3],
                              "is_reveal": beat == "reveal" and i == 0, "claim_id": "", "visual_note": "reaction",
                              "sfx": ""})
                n += k
                i += 1
            if beat == "comeuppance_and_moral":
                lines[0]["claim_id"] = "C1"
            return json.dumps({"lines": lines})
        if "ok" in props:
            return json.dumps({"ok": True})
        raise AssertionError(f"unexpected schema {list(props)}")


class FakeTTS:
    name = "fake"

    def synth(self, text, voice, out_dir, stem, rate_pct=0, pitch_hz=0, lexicon=None, job_id=None):
        words = text.split()
        dur = 0.1 + len(words) * 0.36 * (1 - rate_pct / 100)
        sr = 48000
        t = np.arange(int(dur * sr)) / sr
        f0 = 140 + (hash(voice) % 80)
        y = 0.3 * np.sin(2 * math.pi * f0 * t) * (0.6 + 0.4 * np.sin(2 * math.pi * 3 * t))
        p = Path(out_dir) / f"{stem}.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((y * 32767).astype(np.int16).tobytes())
        return tts_mod.TTSResult(p, dur, tts_mod.estimate_words(text, dur), "fake", voice)


@pytest.fixture
def fake_providers(monkeypatch):
    from shortsengine.config import update_settings
    fl = FakeLLM()
    llm_mod.set_llm(fl)
    tts_mod.set_tts(FakeTTS())
    page = wikipedia.Page("Air Carrier Access Act", 1, 123, "https://en.wikipedia.org/wiki/Air_Carrier_Access_Act",
                          "https://en.wikipedia.org/w/index.php?oldid=123", FACT_TEXT)
    monkeypatch.setattr(wikipedia, "search", lambda q, limit=5: [{"title": "Air Carrier Access Act", "snippet": ""}])
    monkeypatch.setattr(wikipedia, "page", lambda t, max_chars=30000: page)
    monkeypatch.setattr(wikipedia, "most_read", lambda d=1, limit=40: [])
    update_settings({"providers": {"image": "procedural"},
                     "video": {"duration_override_s": 26, "preset": "ultrafast", "fps": 24, "render_workers": 2},
                     "voice": {"words_per_minute": 150}})
    return fl
