"""Metadata generation: title per DNA pattern, honest description with sources and
disclosures, tags/hashtags. The reference channel's 'fair use' disclaimer and edit-hashtag
block are deliberately NOT reproduced (they describe reused footage, which this engine never uses)."""
from __future__ import annotations

import re

from ..dna import load_dna

THEME_TAGS = {
    "prejudice_appearance": ["dontjudge", "prejudice"], "kindness_goodwill": ["kindness", "goodwill"],
    "greed_honesty": ["honesty", "greed"], "respect_authority": ["respect", "abuseofpower"],
    "parenting_family": ["parenting", "family"], "slander_false_accusation": ["falseaccusation", "truth"],
    "bullying_disability": ["antibullying", "inclusion"], "entitlement_boundaries": ["entitlement", "boundaries"],
    "character_motivation": ["character", "motivation"], "quick_thinking_survival": ["quickthinking", "safety"],
}


def build(script: dict, research: dict, topic: dict) -> dict:
    dna = load_dna()
    title = script["title"]
    if len(title) > 100:
        title = title[:97].rstrip() + "..."
    claim = next((c for c in research.get("verified_claims", []) if c["id"] == (script.get("fact_card") or {}).get("claim_id")), None)
    theme_tags = THEME_TAGS.get(topic.get("theme", ""), [re.sub(r"[^a-z]", "", topic.get("theme", "story"))])
    hashtags = ["#Shorts", "#MoralStory", "#Karma"] + [f"#{t}" for t in theme_tags[:2]]
    premise = re.sub(r"\s*Twist:.*$", "", topic.get("premise", "")).strip()
    lines = [
        f"{script['moral']}.",
        "",
        premise,
        "",
        "This is an original fictional dramatization. Characters and events are fictional; any resemblance to real "
        "persons is coincidental. Visuals are AI-generated and voices are synthetic.",
    ]
    if claim:
        lines += ["", f"Real fact: {script['fact_card']['text']}", f"Source: {claim['page_title']} - {claim['permalink']}"]
    lines += ["", " ".join(hashtags)]
    tags = sorted({*theme_tags, "moral story", "karma", "short drama", "life lesson", topic.get("setting", "").replace("_", " ")} - {""})
    return {
        "title": title,
        "description": "\n".join(lines)[:4900],
        "tags": tags[:15],
        "hashtags": hashtags,
        "category_id": "22",  # People & Blogs (reference channel category, 91/91)
        "contains_synthetic_media": True,
        "made_for_kids": False,
        "default_language": dna.language,
    }
