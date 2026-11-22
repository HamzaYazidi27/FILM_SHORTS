"""MODULE 5 - Asset generation, retrieval and rights management.

Every asset used in a render is registered with source, license, license URL, usage
restrictions, generation/download date, associated scene and file hash. User-provided media
is accepted only from data/user_assets/ with a sidecar <file>.license.json declaring rights.
Footage from other creators is never fetched."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from PIL import Image, ImageOps

from .. import costs, db, logs
from ..config import data_dir, load_settings
from ..providers.images import generate_with_fallback

log = logs.get("assets")

ALLOWED_LICENSES_PREFIX = ("AI-generated", "Original", "CC0", "Public domain", "CC BY", "User-owned", "Licensed")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def register(job_id: int | None, key: str, kind: str, path: Path, source: str, license: str, license_url: str = "",
             restrictions: str = "", prompt: str = "", scene: str = "") -> int:
    return db.ex("INSERT INTO assets(job_id,asset_key,kind,path,source,license,license_url,usage_restrictions,prompt,"
                 "scene,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (job_id, key, kind, str(path), source, license, license_url, restrictions, prompt, scene,
                  sha256(path), time.time()))


def cover(im: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.fit(im, (w, h), Image.LANCZOS, centering=(0.5, 0.42))


def user_asset(name: str) -> tuple[Path, dict] | None:
    p = data_dir() / "user_assets" / name
    lic = p.with_suffix(p.suffix + ".license.json")
    if p.exists() and lic.exists():
        return p, json.loads(lic.read_text())
    return None


def acquire(job_id: int, storyboard: dict, workdir: Path) -> dict:
    """Generate or reuse every storyboard asset; returns {asset_key: path}."""
    st = load_settings()
    out_dir = workdir / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    scenes = {}
    for s in storyboard["shots"]:
        scenes.setdefault(s["asset_key"], []).append(s["n"])
    for a in storyboard["assets"]:
        key = a["key"]
        dst = out_dir / f"{key}.png"
        existing = db.q1("SELECT * FROM assets WHERE job_id=? AND asset_key=? ORDER BY id DESC", (job_id, key))
        if existing and Path(existing["path"]).exists():
            result[key] = existing["path"]
            continue
        ua = user_asset(a["prompt"][5:]) if a["prompt"].startswith("user:") else None
        if ua:
            p, lic = ua
            im = Image.open(p).convert("RGB")
            cover(im, st.video.width, st.video.height).save(dst)
            register(job_id, key, "image", dst, "user", lic.get("license", "User-owned"), lic.get("license_url", ""),
                     lic.get("restrictions", ""), str(p), ",".join(map(str, scenes.get(key, []))))
            result[key] = str(dst)
            continue
        costs.check_budget(job_id, costs.IMAGE_PRICES.get(st.providers.image, 0.0))
        r = generate_with_fallback(a["prompt"], st.video.width, st.video.height, a["seed"])
        cover(r.image, st.video.width, st.video.height).save(dst)
        costs.record(r.source.split(":")[0], "image", 1, "image", r.cost_usd, job_id, "assets", {"key": key})
        register(job_id, key, "image", dst, r.source, r.license, r.license_url, r.usage_restrictions, a["prompt"],
                 ",".join(map(str, scenes.get(key, []))))
        result[key] = str(dst)
        log.info("asset %s via %s", key, r.source, extra={"job_id": job_id, "stage": "assets"})
    return result


def rights_report(job_id: int) -> list[dict]:
    rows = db.q("SELECT asset_key, kind, source, license, license_url, usage_restrictions, path, created_at "
                "FROM assets WHERE job_id=? ORDER BY id", (job_id,))
    for r in rows:
        r["allowed"] = r["license"].startswith(ALLOWED_LICENSES_PREFIX)
    return rows
