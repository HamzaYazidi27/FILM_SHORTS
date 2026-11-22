"""LLM provider adapters with a common JSON-output interface.

* AnthropicLLM      - Claude via the official `anthropic` SDK (default; needs ANTHROPIC_API_KEY)
* OpenAICompatLLM   - any OpenAI-compatible /chat/completions endpoint (Ollama/LM Studio on
                      macOS Apple Silicon, vLLM, hosted gateways)
* PollinationsLLM   - keyless hosted OpenAI-compatible endpoint (text.pollinations.ai); used
                      as a development fallback when no key is configured

Every call is costed and logged. JSON output is schema-validated by the caller.
"""
from __future__ import annotations

import functools
import json
import re
import time
from typing import Any

import httpx

from .. import costs, logs
from ..config import load_settings, secret

log = logs.get("llm")


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in text (tolerates code fences and prose)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise LLMError("no JSON found in model output")
    start = min(starts)
    depth, in_str, esc = 0, False, False
    open_c = t[start]
    close_c = "}" if open_c == "{" else "]"
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise LLMError("unterminated JSON in model output")


class BaseLLM:
    name = "base"
    model = ""

    def complete_json(self, system: str, user: str, schema: dict, *, job_id: int | None = None,
                      stage: str | None = None, max_tokens: int = 16000, validate=None, retries: int = 2) -> Any:
        """Return parsed JSON. `validate(obj)` may raise ValueError; the error is fed back to the model."""
        last_err = None
        msg = user
        for attempt in range(retries + 1):
            text = self._complete(system, msg, schema, job_id=job_id, stage=stage, max_tokens=max_tokens)
            try:
                obj = extract_json(text)
                if validate:
                    validate(obj)
                return obj
            except (ValueError, LLMError, json.JSONDecodeError) as e:
                last_err = e
                log.warning("LLM output rejected (attempt %d): %s", attempt + 1, e,
                            extra={"job_id": job_id, "stage": stage})
                msg = (user + "\n\nYour previous answer was rejected for this reason: " + str(e)[:800] +
                       "\nReturn corrected JSON only, matching the schema exactly.")
        raise LLMError(f"{self.name}: invalid output after {retries + 1} attempts: {last_err}")

    def _complete(self, system: str, user: str, schema: dict, **kw) -> str:  # pragma: no cover
        raise NotImplementedError


class AnthropicLLM(BaseLLM):
    name = "anthropic"

    def __init__(self, model: str, effort: str = "high"):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=3)
        self.model = model
        self.effort = effort

    def _complete(self, system, user, schema, *, job_id=None, stage=None, max_tokens=16000) -> str:
        a = self._anthropic
        costs.check_budget(job_id)
        try:
            # Streaming avoids HTTP timeouts on long structured outputs; server-side fallbacks
            # re-run a policy-declined request on Anthropic's recommended fallback model.
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                resp = stream.get_final_message()
        except a.RateLimitError as e:
            raise LLMError(f"rate limited: {e}") from e
        except a.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError(f"Claude API connection error: {e}") from e
        u = resp.usage
        usd = costs.llm_cost(resp.model, u.input_tokens, u.output_tokens)
        costs.record("anthropic", "llm", u.input_tokens + u.output_tokens, "tokens", usd, job_id, stage,
                     {"model": resp.model, "in": u.input_tokens, "out": u.output_tokens})
        if resp.stop_reason == "refusal":
            raise LLMError("model declined the request (refusal)")
        if resp.stop_reason == "max_tokens":
            raise LLMError("output truncated at max_tokens")
        return "".join(b.text for b in resp.content if b.type == "text")


class OpenAICompatLLM(BaseLLM):
    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str | None = None, provider_name: str | None = None,
                 extra: dict | None = None, min_interval_s: float = 0.0):
        self.extra = extra or {}
        self.min_interval_s = min_interval_s
        self._last = 0.0
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        if provider_name:
            self.name = provider_name

    def _complete(self, system, user, schema, *, job_id=None, stage=None, max_tokens=16000) -> str:
        costs.check_budget(job_id)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system + "\n\nRespond with JSON only that validates against this JSON Schema:\n"
                 + json.dumps(schema)},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            **self.extra,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last = None
        for attempt in range(8):
            wait = self._last + self.min_interval_s - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = httpx.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=300)
                self._last = time.time()
                if r.status_code in (402, 429) and ("limit" in r.text.lower()):
                    last = f"rate limited: {r.text[:120]}"
                    time.sleep(max(5.0, self.min_interval_s / 2))
                    continue
                if r.status_code in (500, 502, 503, 504):
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(8 * (attempt + 1))
                    continue
                if r.status_code == 400 and "output_token_limit" in r.text:
                    last = "reasoning exhausted the output token limit"
                    body["max_tokens"] = min(32000, int(body["max_tokens"] * 2))
                    continue
                if r.status_code >= 400:
                    raise LLMError(f"{self.name} HTTP {r.status_code}: {r.text[:300]}")
                d = r.json()
                text = d["choices"][0]["message"].get("content") or ""
                if d["choices"][0].get("finish_reason") == "length":
                    last = "output truncated (finish_reason=length)"
                    body["max_tokens"] = int(body["max_tokens"] * 1.5)
                    continue
                u = d.get("usage") or {}
                costs.record(self.name, "llm", (u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0),
                             "tokens", 0.0, job_id, stage, {"model": d.get("model", self.model)})
                if not text.strip():
                    last = "empty completion"
                    continue
                return text
            except httpx.HTTPError as e:
                last = str(e)
                time.sleep(5 * (attempt + 1))
        raise LLMError(f"{self.name} failed: {last}")


@functools.lru_cache(maxsize=1)
def PollinationsLLM() -> OpenAICompatLLM:
    return OpenAICompatLLM("https://text.pollinations.ai/openai", "openai", secret("POLLINATIONS_API_KEY"),
                           provider_name="pollinations", extra={"reasoning_effort": "low"},
                           min_interval_s=0.0 if secret("POLLINATIONS_API_KEY") else 46.0)


class FallbackLLM(BaseLLM):
    """Try the primary provider, then the fallback, per call."""

    def __init__(self, primary: BaseLLM, fallback: BaseLLM | None):
        self.primary, self.fallback = primary, fallback
        self.name = primary.name + (f"+{fallback.name}" if fallback else "")
        self.model = primary.model

    def complete_json(self, *a, **kw):
        try:
            return self.primary.complete_json(*a, **kw)
        except costs.BudgetExceeded:
            raise
        except Exception as e:
            if not self.fallback:
                raise
            log.warning("primary LLM %s failed (%s); using fallback %s", self.primary.name, e, self.fallback.name,
                        extra={"job_id": kw.get("job_id"), "stage": kw.get("stage")})
            return self.fallback.complete_json(*a, **kw)


def _build(kind: str) -> BaseLLM | None:
    p = load_settings().providers
    if kind == "anthropic":
        return AnthropicLLM(p.llm_model, p.llm_effort) if secret("ANTHROPIC_API_KEY") else None
    if kind == "openai_compatible":
        return OpenAICompatLLM(p.openai_compatible_base_url, p.openai_compatible_model, secret("OPENAI_COMPATIBLE_API_KEY"))
    if kind == "pollinations":
        return PollinationsLLM()
    return None


_override: BaseLLM | None = None


def set_llm(llm: BaseLLM | None) -> None:
    """Inject an LLM (used by tests)."""
    global _override
    _override = llm


def get_llm() -> BaseLLM:
    if _override is not None:
        return _override
    p = load_settings().providers
    primary = _build(p.llm)
    fb = _build(p.llm_fallback) if p.llm_fallback != "none" else None
    if primary is None and fb is None:
        raise LLMError(f"LLM provider '{p.llm}' is not configured (missing key) and no fallback is enabled")
    if primary is None:
        log.warning("LLM provider %s not configured; using fallback %s", p.llm, fb.name)
        return fb
    return FallbackLLM(primary, fb) if fb else primary
