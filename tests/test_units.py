"""Unit tests for critical components."""
from __future__ import annotations

import json
import subprocess
import time

import pytest

from shortsengine import costs, db
from shortsengine.dna import load_dna, reference_titles
from shortsengine.modules import analytics, publish, qc, research, storyboard
from shortsengine.providers import tts
from shortsengine.providers.llm import extract_json
from shortsengine.textutil import fingerprint, jaccard, numbers, quote_in_text, shingle_sim


def test_dna_loads_measured_values():
    d = load_dna()
    assert d.name == "moral_reversal_drama"
    assert 155 <= d.duration_target <= 178
    assert [b.name for b in d.beats][0] == "cold_open_conflict"
    assert d.beats[-1].end_pct == 1.0
    assert d.opening_shot == "medium_two_shot" and d.avoid_opening == "insert_detail"
    assert d.theme_prior("parenting_family") > 0 > d.theme_prior("entitlement_boundaries")
    assert len(reference_titles()) == 91


def test_textutil():
    assert quote_in_text("prohibits  commercial airlines", "It PROHIBITS commercial airlines from x")
    assert not quote_in_text("totally different sentence here", "It prohibits commercial airlines")
    assert numbers("enacted in 1,986 and 3.5") == {"1986", "3.5"}
    assert jaccard("never judge a book", "never judge books by cover") > 0.2
    assert shingle_sim("a b c d e f", "a b c d e f") == 1.0
    assert fingerprint("A moral", "premise") == fingerprint("premise", "a MORAL")


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    assert extract_json('Sure! here: {"a": "x}y"} trailing') == {"a": "x}y"}


def test_claim_verification_rejects_hallucinated_numbers():
    from tests.conftest import FACT_TEXT
    from shortsengine.providers.wikipedia import Page
    pages = {"P": Page("P", 1, 9, "u", "perma", FACT_TEXT)}
    ok = research.verify_claim({"claim": "It was enacted in 1986.", "quote": "It was enacted in 1986.", "page_title": "P"}, pages)
    assert ok["verified"] and ok["permalink"] == "perma"
    bad = research.verify_claim({"claim": "It was enacted in 1999.", "quote": "It was enacted in 1986.", "page_title": "P"}, pages)
    assert not bad["verified"] and "numbers" in bad["reason"]
    fake = research.verify_claim({"claim": "x", "quote": "This sentence is not in the source at all.", "page_title": "P"}, pages)
    assert not fake["verified"] and "quote" in fake["reason"]


def test_caption_chunking():
    words = [{"text": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate("You think you own this plane, lady?".split())]
    ch = storyboard.chunk_caption_words(words, 4)
    assert all(1 <= len(c["words"]) <= 4 for c in ch)
    assert ch[0]["line2"] and ch[0]["line1"].isupper() or ch[0]["line1"] == ""
    assert "PLANE" in " ".join(c["line2"] for c in ch)


def test_align_words_maps_lexicon_changes():
    spoken = [tts.Word("Doctor", 0.0, 0.3), tts.Word("Smith", 0.3, 0.6), tts.Word("arrived", 0.6, 1.0)]
    out = tts.align_words(["Dr.", "Smith", "arrived"], spoken)
    assert [w.text for w in out] == ["Dr.", "Smith", "arrived"] and out[2].start == 0.6


def test_budget_enforcement():
    from shortsengine.config import update_settings
    update_settings({"automation": {"daily_budget_usd": 0.01}})
    costs.record("anthropic", "llm", 1000, "tokens", 0.02)
    with pytest.raises(costs.BudgetExceeded):
        costs.check_budget()
    assert costs.summary()["today_usd"] == 0.02


def test_db_migrations_idempotent():
    c = db.connect()
    assert db.migrate(c) == len(db.MIGRATIONS)
    assert db.q1("SELECT v FROM schema_version")["v"] == len(db.MIGRATIONS)


def _synthetic_video(path, black=False, loud=False):
    vf = "color=c=black:s=360x640:d=2" if black else "testsrc2=s=360x640:d=2"
    af = "sine=f=440:d=2,volume=8" if loud else "sine=f=440:d=2,volume=0.3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", vf, "-f", "lavfi", "-i", af, "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


def test_qc_media_analysis_detects_black_and_clipping(tmp_path):
    good, bad = tmp_path / "good.mp4", tmp_path / "bad.mp4"
    _synthetic_video(good)
    _synthetic_video(bad, black=True, loud=True)
    g, b = qc.analyse_media(good), qc.analyse_media(bad)
    assert not g["black_segments"] and not g["decode_errors"]
    assert b["black_segments"] and b["max_volume_db"] > -0.3


def test_publish_request_body_scheduling_forces_private():
    from datetime import datetime, timezone
    meta = {"title": "t", "description": "d", "tags": ["a"], "category_id": "22", "contains_synthetic_media": True}
    b = publish.request_body(meta, "public", datetime(2030, 1, 1, 11, 5, tzinfo=timezone.utc))
    assert b["status"]["privacyStatus"] == "private" and b["status"]["publishAt"] == "2030-01-01T11:05:00.000Z"
    assert b["status"]["containsSyntheticMedia"] is True
    assert publish.request_body(meta, "unlisted", None)["status"]["privacyStatus"] == "unlisted"


def test_publish_upload_with_fake_client(tmp_path):
    p = tmp_path / "v.mp4"
    _synthetic_video(p)
    vid = db.ex("INSERT INTO videos(job_id,title,description,tags,path,features,created_at) VALUES(?,?,?,?,?,?,?)",
                (None, "T", "D", '["x"]', str(p), json.dumps({"metadata_flags": {"category_id": "22"}}), time.time()))

    class Req:
        def next_chunk(self):
            return None, {"id": "abc123"}

    class Videos:
        def insert(self, part, body, media_body):
            assert body["status"]["privacyStatus"] == "private"
            return Req()

    class YT:
        def videos(self):
            return Videos()

    r = publish.upload(vid, "private", client=YT())
    assert r["youtube_id"] == "abc123"
    assert db.q1("SELECT publish_status FROM videos WHERE id=?", (vid,))["publish_status"] == "uploaded"


def test_analytics_learning_is_shrunk_and_capped():
    now = time.time()
    for i, (theme, views) in enumerate([("kindness_goodwill", 900000), ("kindness_goodwill", 800000),
                                        ("entitlement_boundaries", 2000), ("entitlement_boundaries", 3000),
                                        ("greed_honesty", 50000)]):
        vid = db.ex("INSERT INTO videos(job_id,title,path,features,published_at,created_at) VALUES(?,?,?,?,?,?)",
                    (None, f"v{i}", "x", json.dumps({"theme": theme}), now - 5 * 86400, now))
        db.ex("INSERT INTO analytics(video_id,fetched_at,views,likes,comments) VALUES(?,?,?,?,?)", (vid, now, views, 0, 0))
    s = analytics.learn()
    assert s["themes"]["kindness_goodwill"] > 0 > s["themes"]["entitlement_boundaries"]
    assert all(abs(v) <= analytics.CAP for v in s["themes"].values())
    assert "greed_honesty" not in s["themes"]  # n=1 never changes the strategy
    assert db.kv_get("strategy")["n_videos"] == 5


def test_schema_validation_and_coercion():
    from shortsengine.providers.llm import coerce, schema_errors
    sch = {"type": "object", "required": ["a", "b"], "properties": {
        "a": {"type": "number"}, "b": {"type": "array", "items": {"type": "string", "enum": ["x", "y"]}}}}
    assert schema_errors(coerce({"a": "0.5", "b": ["x"]}, sch), sch) == []
    errs = schema_errors({"b": ["z"]}, sch)
    assert "$.a is required" in errs and any("one of" in e for e in errs)
