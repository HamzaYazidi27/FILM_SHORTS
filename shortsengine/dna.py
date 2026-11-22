"""Channel DNA loader: turns research/channel_dna.json into typed parameters for the engine.

Nothing about the niche is hard-coded in the engine: themes, settings, durations, pacing,
caption style, grade, title patterns and narrative beats all come from this file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .config import ROOT, load_settings


@dataclass
class Beat:
    name: str
    start_pct: float
    end_pct: float
    notes: str = ""


@dataclass
class FormatDNA:
    name: str
    raw: dict
    language: str
    duration_target: float
    duration_min: float
    duration_max: float
    voice_mode: str                    # multi_character_dialogue | single_narrator
    beats: list[Beat]
    reveal_target_pct: float
    themes: list[dict]
    settings_prior: list[dict]
    mean_shot_s: float
    shot_s_range: tuple[float, float]
    opening_shot: str
    avoid_opening: str
    ending_shot: str
    shot_mix: dict[str, float]
    grade: dict
    title_bar: dict
    captions: dict
    transitions: dict
    title_pattern: str
    title_grammar_preference: list[str]
    moral_words: dict
    publish_hour_window: list[int]
    music: dict
    originality_rules: list[str] = field(default_factory=list)

    def theme_prior(self, theme: str) -> float:
        """Age-adjusted log-view residual median for a theme (0 = era median), shrunk by sample size."""
        for t in self.themes:
            if t["theme"] == theme:
                n = t.get("n", 1)
                return t["age_adj_median"] * n / (n + 3)   # shrinkage toward 0 for small n
        return 0.0

    def setting_prior(self, setting: str) -> float:
        for s in self.settings_prior:
            if s["setting"] == setting:
                n = s.get("n", 1)
                return s["age_adj_median"] * n / (n + 3)
        return 0.0

    def beat_at(self, pct: float) -> str:
        for b in self.beats:
            if b.start_pct <= pct < b.end_pct:
                return b.name
        return self.beats[-1].name


def _f(d: dict, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


@lru_cache(maxsize=8)
def _load(path: str, fmt: str | None) -> FormatDNA:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    data = json.loads(p.read_text())
    name = fmt or data["active_format"]
    f = data["formats"][name]
    nar = f.get("narrative", {})
    vis = f.get("visual", {})
    beats = [Beat(b["beat"], b["start_pct"], b["end_pct"], b.get("notes", "")) for b in nar.get("structure", [])]
    if not beats:
        beats = [Beat("hook", 0, 0.1), Beat("body", 0.1, 0.85), Beat("payoff", 0.85, 1.0)]
    dur = f["duration_s"]
    shot = vis.get("mean_shot_s", {})
    target_shot = shot.get("target", 2.6)
    return FormatDNA(
        name=name,
        raw=f,
        language=f.get("language", "en"),
        duration_target=dur["target"],
        duration_min=dur.get("min", dur["target"] * 0.85),
        duration_max=dur.get("max", dur["target"] * 1.05),
        voice_mode=nar.get("voice_mode", "single_narrator"),
        beats=beats,
        reveal_target_pct=_f(nar, "reveal_position_pct", "target", default=0.82),
        themes=nar.get("themes_ranked_by_evidence", []),
        settings_prior=nar.get("settings_ranked_by_evidence", []),
        mean_shot_s=target_shot,
        shot_s_range=(max(1.0, target_shot * 0.45), target_shot * 1.75),
        opening_shot=_f(vis, "opening_shot", "preferred", default="medium_two_shot"),
        avoid_opening=_f(vis, "opening_shot", "avoid", default="insert_detail"),
        ending_shot=_f(vis, "ending_shot", "preferred", default="closeup_reaction"),
        shot_mix=vis.get("shot_mix", {"closeup_single": 0.6, "medium_single": 0.2, "medium_two_shot": 0.1, "wide_group": 0.1}),
        grade=vis.get("grade", {}),
        title_bar=vis.get("title_bar", {"present_ratio": 0}),
        captions=f.get("captions", {}),
        transitions=f.get("transitions", {"default": "hard_cut"}),
        title_pattern=_f(f, "title", "pattern", default="{moral}"),
        title_grammar_preference=_f(f, "title", "grammar_preference", default=[]),
        moral_words=_f(f, "title", "moral_words", default={"median": 8, "min": 4, "max": 14}),
        publish_hour_window=_f(f, "cadence", "publish_hour_utc_window", default=[10, 12]),
        music=f.get("audio", {}),
        originality_rules=_f(data, "originality_policy", "engine_rules", default=[]),
    )


def load_dna(path: str | None = None, fmt: str | None = None) -> FormatDNA:
    s = load_settings()
    return _load(path or s.dna_path, fmt or s.format)


def reference_titles() -> list[str]:
    """Titles of the reference channel, used only to block copying."""
    p = ROOT / "research" / "video_dataset.json"
    if not p.exists():
        return []
    return [r.get("clean_title", "") for r in json.loads(p.read_text())]
