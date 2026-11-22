"""`python -m shortsengine doctor` - verifies the machine can run the selected providers and prints
exact fixes. `python -m shortsengine profile <name>` switches between provider presets."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys

from .config import data_dir, load_settings, update_settings

PROFILES = {
    # fully local, no API keys (recommended on Apple Silicon with >= 16 GB RAM; 32 GB ideal)
    "local": {"providers": {"llm": "ollama", "llm_fallback": "none", "image": "mflux", "image_fallback": "procedural"},
              "voice": {"provider": "kokoro"}},
    # local LLM + Draw Things / Forge image server instead of mflux
    "local-drawthings": {"providers": {"llm": "ollama", "llm_fallback": "none", "image": "a1111",
                                       "a1111_base_url": "http://127.0.0.1:7860", "image_fallback": "procedural"},
                         "voice": {"provider": "kokoro"}},
    # no keys, but uses free hosted services (rate-limited)
    "free-cloud": {"providers": {"llm": "pollinations", "llm_fallback": "none", "image": "pollinations",
                                 "image_fallback": "procedural"}, "voice": {"provider": "edge"}},
    # Claude API (needs ANTHROPIC_API_KEY), local voice and images
    "claude": {"providers": {"llm": "anthropic", "llm_model": "claude-opus-5-5", "llm_fallback": "ollama", "image": "mflux",
                             "image_fallback": "procedural"}, "voice": {"provider": "kokoro"}},
}


def apply_profile(name: str) -> dict:
    if name not in PROFILES:
        raise SystemExit(f"unknown profile {name}; choose from {sorted(PROFILES)}")
    update_settings(PROFILES[name])
    return PROFILES[name]


def _ok(name: str, ok: bool, detail: str, fix: str = "") -> dict:
    return {"check": name, "ok": ok, "detail": detail, "fix": "" if ok else fix}


def run_checks(deep: bool = False) -> list[dict]:
    s = load_settings()
    res = []
    res.append(_ok("python", sys.version_info >= (3, 11), sys.version.split()[0], "install Python 3.11+ (brew install python@3.12)"))
    ff = shutil.which("ffmpeg")
    enc = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout if ff else ""
    res.append(_ok("ffmpeg + libx264", bool(ff) and "libx264" in enc, ff or "missing", "brew install ffmpeg"))
    free = shutil.disk_usage(data_dir()).free / 1e9
    res.append(_ok("disk space", free > 25, f"{free:.0f} GB free in {data_dir()}", "free at least 25 GB (FLUX weights + renders)"))
    if platform.system() == "Darwin":
        try:
            mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2**30
            res.append(_ok("memory", mem >= 16, f"{mem:.0f} GB unified memory ({platform.machine()})",
                           "16 GB+ recommended; use a smaller model (qwen2.5:7b) and mflux_quantize=4"))
        except ValueError:
            pass
    p, v = s.providers, s.voice
    # LLM
    if p.llm == "ollama":
        from .providers.llm import LLMError, OllamaLLM
        try:
            OllamaLLM(p.ollama_base_url, p.ollama_model).check()
            res.append(_ok("LLM (ollama)", True, f"{p.ollama_model} @ {p.ollama_base_url}"))
        except LLMError as e:
            res.append(_ok("LLM (ollama)", False, str(e), f"brew install ollama && ollama serve & ollama pull {p.ollama_model}"))
    elif p.llm == "anthropic":
        from .config import secret
        res.append(_ok("LLM (anthropic)", bool(secret("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY", "add the key to .env"))
    else:
        res.append(_ok(f"LLM ({p.llm})", True, "hosted, no key required"))
    # images
    if p.image == "mflux":
        okm = bool(shutil.which("mflux-generate"))
        res.append(_ok("images (mflux FLUX.1-schnell)", okm, shutil.which("mflux-generate") or "missing",
                       "pip install mflux   (macOS Apple Silicon only; first image downloads the model once)"))
    elif p.image == "a1111":
        import httpx
        try:
            httpx.get(p.a1111_base_url + "/sdapi/v1/sd-models", timeout=5)
            res.append(_ok("images (a1111/Draw Things)", True, p.a1111_base_url))
        except httpx.HTTPError as e:
            res.append(_ok("images (a1111/Draw Things)", False, str(e),
                           "Draw Things: Settings > API Server > enable HTTP on port 7860"))
    else:
        res.append(_ok(f"images ({p.image})", True, "configured"))
    # voice
    if v.provider == "kokoro":
        try:
            import kokoro_onnx  # noqa: F401
            from .providers.tts import ensure_kokoro_files
            if deep:
                ensure_kokoro_files(v.kokoro_model)
            from .providers.tts import kokoro_dir
            have = (kokoro_dir() / v.kokoro_model).exists()
            res.append(_ok("voice (kokoro)", True, f"model {'present' if have else 'downloads on first use'} in {kokoro_dir()}"))
        except ImportError:
            res.append(_ok("voice (kokoro)", False, "kokoro-onnx missing", "pip install 'kokoro-onnx' soundfile"))
    else:
        res.append(_ok(f"voice ({v.provider})", True, "configured"))
    return res


def print_report(rows: list[dict]) -> int:
    for r in rows:
        print(f"[{'OK ' if r['ok'] else 'FIX'}] {r['check']:<30} {r['detail']}")
        if r["fix"]:
            print(f"      -> {r['fix']}")
    bad = sum(not r["ok"] for r in rows)
    print("\nAll good - run: python -m shortsengine serve" if not bad else f"\n{bad} item(s) need attention.")
    return 1 if bad else 0
