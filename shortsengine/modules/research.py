"""MODULE 2 - Research and fact-checking agent.

For the selected topic, the agent:
1. asks the LLM for concrete claims + Wikipedia queries for the topic's fact angle,
2. fetches candidate pages (with permanent revision links),
3. asks the LLM to extract claims WITH verbatim supporting quotes from the fetched text,
4. verifies programmatically that each quote exists in the fetched page and that every
   number/date in the claim appears in the quote (no hallucinated specifics),
5. flags speculative wording and asks the LLM to list contradictions between sources,
6. passes only if at least one verified, non-speculative claim remains.
The script stage may only state facts that reference a verified claim id."""
from __future__ import annotations

import json
import re

from .. import logs
from ..config import load_settings
from ..providers import wikipedia
from ..providers.llm import get_llm
from ..textutil import is_speculative, numbers, quote_in_text, tokens

log = logs.get("research")


class ResearchRejected(RuntimeError):
    pass


QUERY_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"], "additionalProperties": False,
}
CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {"type": "array", "items": {
            "type": "object",
            "properties": {"claim": {"type": "string"}, "quote": {"type": "string"}, "page_title": {"type": "string"}},
            "required": ["claim", "quote", "page_title"], "additionalProperties": False}},
        "contradictions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["claims", "contradictions"], "additionalProperties": False,
}


def relevant_excerpt(text: str, query: str, max_chars: int = 6000) -> str:
    """Keep the paragraphs that share most terms with the query (keeps prompts small)."""
    q = set(tokens(query))
    paras = [p.strip() for p in re.split(r"\n{1,}", text) if len(p.strip()) > 40]
    ranked = sorted(paras, key=lambda p: -len(q & set(tokens(p))))
    out, n = [], 0
    for p in ranked:
        if n + len(p) > max_chars:
            continue
        out.append(p)
        n += len(p)
    return "\n\n".join(out)


def verify_claim(c: dict, pages: dict[str, wikipedia.Page]) -> dict:
    page = pages.get(c["page_title"]) or next((p for t, p in pages.items() if t.lower() == c["page_title"].lower()), None)
    res = dict(c)
    res["verified"] = False
    if not page:
        res["reason"] = "cited page was not fetched"
        return res
    res.update({"url": page.url, "permalink": page.permalink, "revid": page.revid})
    if not quote_in_text(c["quote"], page.text):
        res["reason"] = "quote not found verbatim in source"
        return res
    missing = numbers(c["claim"]) - numbers(c["quote"])
    if missing:
        res["reason"] = f"claim contains numbers not in the quote: {sorted(missing)}"
        return res
    res["speculative"] = is_speculative(c["quote"]) or is_speculative(c["claim"])
    res["verified"] = not res["speculative"]
    if res["speculative"]:
        res["reason"] = "speculative wording"
    return res


def run(topic: dict, job_id: int | None = None) -> dict:
    llm = get_llm()
    fa = json.loads(topic.get("fact_angle") or "{}")
    q = llm.complete_json(
        "You are a meticulous fact-checker who only trusts encyclopedic sources.",
        f"Story premise: {topic['premise']}\nFact angle to verify: {fa.get('fact')}\nSuggested query: {fa.get('query')}\n"
        "Return up to 3 English Wikipedia search queries most likely to find an article that states this fact "
        "explicitly (article-title style queries work best).",
        QUERY_SCHEMA, job_id=job_id, stage="research", max_tokens=2000)
    queries = [x for x in q["queries"][:3] if x.strip()] or [fa.get("query", topic["moral"])]
    pages: dict[str, wikipedia.Page] = {}
    searched = []
    for query in queries:
        for hit in wikipedia.search(query, limit=2):
            if hit["title"] in pages:
                continue
            p = wikipedia.page(hit["title"])
            if p:
                pages[p.title] = p
        searched.append(query)
        if len(pages) >= 4:
            break
    if not pages:
        raise ResearchRejected("no Wikipedia sources found for the fact angle")
    excerpts = "\n\n".join(f"=== SOURCE: {t} ===\n{relevant_excerpt(p.text, fa.get('fact', '') + ' ' + ' '.join(queries))}"
                           for t, p in pages.items())
    ext = llm.complete_json(
        "You extract facts strictly from provided sources. A claim is only allowed if a sentence in the source "
        "states it. Copy the supporting sentence(s) EXACTLY, character for character, as 'quote'. Never add numbers, "
        "dates or names that are not in the quote. Prefer simple, durable facts (rules, definitions, history).",
        f"Fact angle: {fa.get('fact')}\nStory premise (fiction; do not verify the story itself): {topic['premise']}\n\n"
        f"{excerpts}\n\nReturn 1-4 claims useful as the real-world takeaway of this story, each with an exact quote "
        "and the SOURCE title. Also list any contradictions between sources about these claims (empty list if none).",
        CLAIMS_SCHEMA, job_id=job_id, stage="research", max_tokens=6000)
    checked = [verify_claim(c, pages) for c in ext["claims"]]
    for i, c in enumerate(checked, 1):
        c["id"] = f"C{i}"
    verified = [c for c in checked if c["verified"]]
    result = {
        "topic_id": topic.get("id"),
        "fact_angle": fa,
        "queries": searched,
        "sources": [{"title": t, "url": p.url, "permalink": p.permalink, "revid": p.revid} for t, p in pages.items()],
        "claims": checked,
        "verified_claims": verified,
        "contradictions": ext.get("contradictions", []),
        "passed": bool(verified) or not load_settings().automation.require_fact_anchor,
    }
    if result["contradictions"]:
        # a contradiction that mentions a verified claim's key terms invalidates that claim
        for c in verified:
            key = set(tokens(c["claim"]))
            for con in result["contradictions"]:
                if len(key & set(tokens(con))) >= max(2, len(key) // 3):
                    c["verified"] = False
                    c["reason"] = f"contradicted: {con[:200]}"
        result["verified_claims"] = [c for c in verified if c["verified"]]
        result["passed"] = bool(result["verified_claims"]) or not load_settings().automation.require_fact_anchor
    log.info("research: %d/%d claims verified from %d sources", len(result["verified_claims"]), len(checked), len(pages),
             extra={"job_id": job_id, "stage": "research"})
    if not result["passed"]:
        reasons = "; ".join(f"{c['claim'][:60]}: {c.get('reason')}" for c in checked) or "no claims extracted"
        raise ResearchRejected(f"topic could not be sufficiently verified ({reasons})")
    return result
