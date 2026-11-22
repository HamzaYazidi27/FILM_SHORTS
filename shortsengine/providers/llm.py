"""LLM provider adapters with a common JSON-output interface.

* AnthropicLLM      - Claude via the official `anthropic` SDK (default; needs ANTHROPIC_API_KEY)
* OpenAICompatLLM   - any OpenAI-compatible /chat/completions endpoint (Ollama/LM Studio on
                      macOS Apple Silicon, vLLM, hosted gateways)
* OllamaLLM         - fully local models via Ollama (default; Apple Silicon friendly)
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


def schema_errors(obj: Any, schema: dict, path: str = "$") -> list[str]:
    """Minimal JSON-Schema check (type, required, enum, items) used for providers that do not
    enforce structured output server-side."""
    errs: list[str] = []
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}
    if t and not isinstance(obj, types[t]) or (t in ("integer", "number") and isinstance(obj, bool)):
        return [f"{path} must be {t}"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path} must be one of {schema['enum']}")
    if t == "object":
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}.{k} is required")
        for k, sub in schema.get("properties", {}).items():
            if k in obj:
                errs += schema_errors(obj[k], sub, f"{path}.{k}")
    elif t == "array" and "items" in schema:
        for i, it in enumerate(obj):
            errs += schema_errors(it, schema["items"], f"{path}[{i}]")
    return errs


def coerce(obj: Any, schema: dict) -> Any:
    """Fix harmless type slips (e.g. "0.8" for a number, 34.0 for an integer) before validation."""
    t = schema.get("type")
    try:
        if t == "number" and isinstance(obj, str):
            return float(obj)
        if t == "integer" and isinstance(obj, (str, float)) and not isinstance(obj, bool):
            return int(float(obj))
        if t == "boolean" and isinstance(obj, str):
            return obj.strip().lower() == "true"
        if t == "string" and obj is None:
            return ""
    except ValueError:
        return obj
    if t == "object" and isinstance(obj, dict):
        return {k: coerce(v, schema.get("properties", {}).get(k, {})) for k, v in obj.items()}
    if t == "array" and isinstance(obj, list):
        return [coerce(v, schema.get("items", {})) for v in obj]
    return obj


def salvage_truncated_json(text: str) -> Any:
    """Recover the longest valid prefix of a truncated JSON document by cutting after the last
    complete value and closing the open brackets (e.g. keep 9 complete dialogue lines of 10)."""
    t = text[text.find("{"):] if "{" in text else text
    stack, in_str, esc, cuts = [], False, False, []
    for i, c in enumerate(t):
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
        elif c in "{[":
            stack.append(c)
        elif c in "}]":
            if stack:
                stack.pop()
            cuts.append((i + 1, list(stack)))
    for pos, st in reversed(cuts):
        closer = "".join("}" if b == "{" else "]" for b in reversed(st))
        try:
            return json.loads(t[:pos] + closer)
        except json.JSONDecodeError:
            continue
    raise LLMError("could not salvage truncated JSON")


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
                try:
                    raw = extract_json(text)
                except (LLMError, json.JSONDecodeError):
                    raw = salvage_truncated_json(text)
                    log.warning("salvaged truncated JSON output", extra={"job_id": job_id, "stage": stage})
                obj = coerce(raw, schema)
                errs = schema_errors(obj, schema)
                if errs:
                    raise ValueError("schema violations: " + "; ".join(errs[:10]))
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
                    if text.strip().startswith(("{", "[", "```")) and len(text) > 200:
                        return text  # truncated JSON: complete_json salvages the complete part
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


class OllamaLLM(BaseLLM):
    """Local LLM through Ollama's native API. `format` carries the JSON schema, so the model is
    grammar-constrained to valid JSON (reliable even for 7-14B models). No API key, no cloud."""
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5:14b", num_ctx: int = 16384):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx

    def check(self) -> dict:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            names = [m["name"] for m in r.json().get("models", [])]
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama is not reachable at {self.base_url} ({e}). Start the Ollama app or run "
                           f"`ollama serve`.") from e
        if not any(n == self.model or n.split(":")[0] == self.model or n == f"{self.model}:latest" for n in names):
            raise LLMError(f"Ollama model '{self.model}' is not installed. Run: ollama pull {self.model} "
                           f"(installed: {names[:8]})")
        return {"ok": True, "models": names}

    def _complete(self, system, user, schema, *, job_id=None, stage=None, max_tokens=16000) -> str:
        body = {"model": self.model, "stream": False, "format": schema, "think": False,
                "messages": [{"role": "system", "content": system + "\nReply with JSON matching the schema."},
                             {"role": "user", "content": user}],
                "options": {"num_ctx": self.num_ctx, "num_predict": min(max_tokens, 8192), "temperature": 0.7}}
        last = None
        for attempt in range(3):
            try:
                r = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=httpx.Timeout(1800, connect=10))
                if r.status_code == 400 and "think" in r.text:   # model without thinking support
                    body.pop("think", None)
                    continue
                if r.status_code == 404:
                    raise LLMError(f"Ollama model '{self.model}' not found. Run: ollama pull {self.model}")
                if r.status_code >= 400:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(3 * (attempt + 1))
                    continue
                d = r.json()
                costs.record("ollama", "llm", (d.get("prompt_eval_count") or 0) + (d.get("eval_count") or 0), "tokens",
                             0.0, job_id, stage, {"model": self.model, "seconds": round((d.get("total_duration") or 0) / 1e9, 1)})
                if d.get("done_reason") == "length":
                    last = "output truncated (num_predict)"
                    body["options"]["num_predict"] = min(16384, body["options"]["num_predict"] * 2)
                    continue
                return d.get("message", {}).get("content", "")
            except httpx.ConnectError as e:
                raise LLMError(f"Ollama is not running at {self.base_url}: start the Ollama app or `ollama serve`") from e
            except httpx.HTTPError as e:
                last = str(e)
        raise LLMError(f"ollama failed: {last}")


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
    if kind == "ollama":
        return OllamaLLM(p.ollama_base_url, p.ollama_model, p.ollama_num_ctx)
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
