"""Image generation adapters. Every result carries license metadata for the asset registry.

* MfluxImages       - FLUX.1-schnell (Apache-2.0) run locally on Apple Silicon via MLX (default)
* A1111Images        - any local Automatic1111-compatible HTTP API (Draw Things, Forge, SD.Next)
* PollinationsImages - hosted FLUX-class generation (keyless free tier is rate-limited and
                       stamps a small corner logo, which the adapter crops away)
* OpenAIImages       - OpenAI Images API (needs OPENAI_API_KEY)
* ProceduralImages   - original, deterministic, offline scene cards rendered with Pillow
                       (always available; used as a fallback and in tests)
"""
from __future__ import annotations

import base64
import hashlib
import io
import random
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter

from .. import costs, logs
from ..config import load_settings, secret

log = logs.get("images")


@dataclass
class ImageResult:
    image: Image.Image
    source: str
    license: str
    license_url: str
    usage_restrictions: str
    cost_usd: float = 0.0


class ImageError(RuntimeError):
    pass


class MfluxImages:
    """Local FLUX.1-schnell through the `mflux` CLI (pip install mflux; macOS Apple Silicon).
    First use downloads the model weights from Hugging Face once; afterwards fully offline."""
    name = "mflux"

    def __init__(self, model: str = "schnell", quantize: int = 4, steps: int = 4, model_path: str = ""):
        import shutil
        self.bin = shutil.which("mflux-generate")
        if not self.bin:
            raise ImageError("mflux not installed (macOS Apple Silicon): pip install mflux")
        self.model, self.quantize, self.steps, self.model_path = model, quantize, steps, model_path

    def generate(self, prompt: str, width: int, height: int, seed: int) -> ImageResult:
        import subprocess
        import tempfile
        w, h = (576, 1024) if width < height else (1024, 576)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "img.png"
            cmd = [self.bin, "--model", self.model, "--prompt", prompt[:1500], "--steps", str(self.steps),
                   "--seed", str(seed), "--width", str(w), "--height", str(h), "--output", str(out)]
            if self.model_path:
                cmd += ["--path", self.model_path]
            else:
                cmd += ["--quantize", str(self.quantize)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if r.returncode != 0 or not out.exists():
                raise ImageError(f"mflux failed: {(r.stderr or r.stdout)[-400:]}")
            im = Image.open(out).convert("RGB")
        return ImageResult(im, "mflux:flux1-" + self.model, "AI-generated (FLUX.1-schnell, Apache-2.0, local)",
                           "https://huggingface.co/black-forest-labs/FLUX.1-schnell",
                           "Original local AI output; Apache-2.0 model weights")


class A1111Images:
    """Local Stable-Diffusion-style server with the Automatic1111 txt2img API
    (Draw Things 'HTTP API server', Forge, SD.Next). License depends on the model the user loads."""
    name = "a1111"

    def __init__(self, base_url: str, steps: int, license_text: str):
        self.base_url, self.steps, self.license_text = base_url.rstrip("/"), steps, license_text

    def generate(self, prompt: str, width: int, height: int, seed: int) -> ImageResult:
        w, h = (576, 1024) if width < height else (1024, 576)
        try:
            r = httpx.post(f"{self.base_url}/sdapi/v1/txt2img", timeout=1800, json={
                "prompt": prompt, "negative_prompt": "text, watermark, logo, deformed hands, extra fingers, blurry",
                "width": w, "height": h, "steps": self.steps, "seed": seed, "cfg_scale": 2.0, "batch_size": 1})
        except httpx.HTTPError as e:
            raise ImageError(f"local image server not reachable at {self.base_url}: {e}") from e
        if r.status_code != 200:
            raise ImageError(f"a1111 HTTP {r.status_code}: {r.text[:200]}")
        im = Image.open(io.BytesIO(base64.b64decode(r.json()["images"][0]))).convert("RGB")
        return ImageResult(im, "a1111:local", self.license_text, "", "Depends on the locally loaded model's license")


class PollinationsImages:
    name = "pollinations"

    def __init__(self, model: str = "flux"):
        self.model = model
        self.key = secret("POLLINATIONS_API_KEY")

    def generate(self, prompt: str, width: int, height: int, seed: int) -> ImageResult:
        # generate at a native-ish portrait size; the editor upscales to the output size
        w, h = (576, 1024) if width < height else (1024, 576)
        url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:1800]) +
               f"?width={w}&height={h}&seed={seed}&nologo=true&model={self.model}")
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        last = None
        for attempt in range(8):
            try:
                r = httpx.get(url, headers=headers, timeout=180, follow_redirects=True)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                    im = Image.open(io.BytesIO(r.content)).convert("RGB")
                    if not self.key:
                        # free tier stamps a logo in the bottom-right corner: crop the bottom 7%
                        im = im.crop((0, 0, im.width, int(im.height * 0.93)))
                    return ImageResult(im, "pollinations:" + self.model, "AI-generated (Pollinations terms)",
                                       "https://pollinations.ai/terms",
                                       "Original AI output; check provider terms before commercial use")
                last = f"HTTP {r.status_code} {r.text[:160]}"
            except httpx.HTTPError as e:
                last = str(e)
            time.sleep(min(60, 12 * (attempt + 1)))
        raise ImageError(f"pollinations failed: {last}")


class OpenAIImages:
    name = "openai"

    def __init__(self, model: str = "gpt-image-1"):
        self.model = model
        self.key = secret("OPENAI_API_KEY")
        if not self.key:
            raise ImageError("OPENAI_API_KEY not configured")

    def generate(self, prompt: str, width: int, height: int, seed: int) -> ImageResult:
        size = "1024x1536" if height > width else "1536x1024"
        r = httpx.post("https://api.openai.com/v1/images/generations",
                       headers={"Authorization": f"Bearer {self.key}"},
                       json={"model": self.model, "prompt": prompt, "size": size, "n": 1}, timeout=300)
        if r.status_code != 200:
            raise ImageError(f"openai images HTTP {r.status_code}: {r.text[:200]}")
        b64 = r.json()["data"][0]["b64_json"]
        im = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        return ImageResult(im, "openai:" + self.model, "AI-generated (OpenAI terms: output owned by user)",
                           "https://openai.com/policies/terms-of-use", "Subject to OpenAI usage policies",
                           costs.IMAGE_PRICES["openai"])


# ---- procedural fallback: original stylised scene cards -------------------------------------

SETTING_PALETTES = {
    "airplane": [(214, 222, 230), (120, 140, 165)],
    "retail_store": [(238, 226, 214), (170, 130, 110)],
    "restaurant": [(90, 60, 45), (210, 160, 110)],
    "street": [(170, 190, 205), (90, 95, 105)],
    "hospital": [(225, 238, 240), (120, 170, 180)],
    "office": [(220, 225, 230), (110, 120, 140)],
}


class ProceduralImages:
    """Draws an original stylised frame: gradient set, soft bokeh lights, and flat figure
    silhouettes in the requested framing. Deterministic per (prompt, seed)."""
    name = "procedural"

    def generate(self, prompt: str, width: int, height: int, seed: int) -> ImageResult:
        rnd = random.Random(int(hashlib.sha256(f"{prompt}|{seed}".encode()).hexdigest()[:12], 16))
        pl = next((v for k, v in SETTING_PALETTES.items() if k.replace("_", " ") in prompt.lower()),
                  [(200, 205, 215), (90, 100, 120)])
        w, h = width // 2, height // 2
        im = Image.new("RGB", (w, h))
        px = im.load()
        top, bot = pl
        for y in range(h):
            t = y / h
            c = tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3))
            for x in range(w):
                px[x, y] = c
        d = ImageDraw.Draw(im, "RGBA")
        for _ in range(14):  # bokeh
            r = rnd.randint(w // 30, w // 10)
            x, y = rnd.randint(0, w), rnd.randint(0, int(h * 0.6))
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 245, 220, rnd.randint(25, 70)))
        p = prompt.lower()
        n = 3 if ("group" in p or "wide" in p) else 2 if ("two" in p or "two-shot" in p) else 1
        close = "close" in p
        for i in range(n):
            cx = w * (i + 1) / (n + 1) + rnd.randint(-w // 20, w // 20)
            scale = (1.9 if close else 1.0) * (0.9 if n > 1 else 1.0)
            head = int(w * 0.11 * scale)
            cy = int(h * (0.36 if close else 0.42))
            tone = rnd.choice([(92, 62, 45), (160, 115, 85), (225, 185, 150), (120, 85, 60)])
            cloth = tuple(rnd.randint(30, 200) for _ in range(3))
            d.ellipse((cx - head * 1.6, cy + head * 0.9, cx + head * 1.6, cy + head * 5.5), fill=cloth + (255,))
            d.rectangle((cx - head * 0.35, cy + head * 0.7, cx + head * 0.35, cy + head * 1.2), fill=tone + (255,))
            d.ellipse((cx - head * 0.8, cy - head, cx + head * 0.8, cy + head), fill=tone + (255,))
            d.chord((cx - head * 0.85, cy - head * 1.15, cx + head * 0.85, cy + head * 0.4), 180, 360,
                    fill=tuple(max(0, c - 70) for c in tone) + (255,))
        im = im.filter(ImageFilter.GaussianBlur(1.2)).resize((width, height), Image.LANCZOS)
        return ImageResult(im, "procedural", "Original (generated by this software)", "",
                           "None - created locally by ShortsEngine")


def get_image_provider(kind: str | None = None):
    p = load_settings().providers
    kind = kind or p.image
    if kind == "mflux":
        return MfluxImages(p.mflux_model, p.mflux_quantize, p.mflux_steps, p.mflux_model_path)
    if kind == "a1111":
        return A1111Images(p.a1111_base_url, p.a1111_steps, p.local_image_license)
    if kind == "pollinations":
        return PollinationsImages(p.image_model)
    if kind == "openai":
        return OpenAIImages()
    return ProceduralImages()


def generate_with_fallback(prompt: str, width: int, height: int, seed: int) -> ImageResult:
    p = load_settings().providers
    try:
        return get_image_provider().generate(prompt, width, height, seed)
    except Exception as e:
        if p.image_fallback == "procedural":
            log.warning("image provider %s failed (%s); using procedural fallback", p.image, e)
            return ProceduralImages().generate(prompt, width, height, seed)
        raise
