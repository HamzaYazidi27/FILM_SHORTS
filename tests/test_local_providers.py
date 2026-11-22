"""Local (no-API-key) providers: Ollama LLM and A1111/Draw Things images are tested against
in-process HTTP servers that implement the same endpoints; Kokoro runs for real when its model
files are available (KOKORO_MODEL_DIR), otherwise the test is skipped."""
from __future__ import annotations

import base64
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

from shortsengine.providers import images, llm


class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        code, obj = self.routes[("GET", self.path)](None)
        self._send(code, obj)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.seen.append((self.path, body))
        code, obj = self.routes[("POST", self.path)](body)
        self._send(code, obj)


@pytest.fixture
def server():
    _Handler.routes, _Handler.seen = {}, []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _Handler
    srv.shutdown()


def test_ollama_structured_output_and_cost_record(server):
    url, h = server
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    h.routes[("GET", "/api/tags")] = lambda b: (200, {"models": [{"name": "qwen2.5:14b"}]})
    h.routes[("POST", "/api/chat")] = lambda b: (200, {"message": {"content": '{"ok": true}'}, "done_reason": "stop",
                                                       "prompt_eval_count": 12, "eval_count": 5})
    m = llm.OllamaLLM(url, "qwen2.5:14b")
    assert m.check()["ok"]
    assert m.complete_json("sys", "user", schema) == {"ok": True}
    path, body = h.seen[-1]
    assert path == "/api/chat" and body["format"] == schema and body["stream"] is False
    assert body["options"]["num_ctx"] == 16384
    from shortsengine import db
    assert db.q1("SELECT provider, usd FROM costs ORDER BY id DESC")["provider"] == "ollama"


def test_ollama_reports_missing_model_and_server(server):
    url, h = server
    h.routes[("GET", "/api/tags")] = lambda b: (200, {"models": [{"name": "llama3.1:8b"}]})
    with pytest.raises(llm.LLMError, match="ollama pull qwen2.5:14b"):
        llm.OllamaLLM(url, "qwen2.5:14b").check()
    with pytest.raises(llm.LLMError, match="not reachable|not running"):
        llm.OllamaLLM("http://127.0.0.1:9", "qwen2.5:14b").check()


def test_ollama_is_the_default_provider():
    from shortsengine.config import Settings
    s = Settings()
    assert (s.providers.llm, s.providers.image, s.voice.provider) == ("ollama", "mflux", "kokoro")
    assert s.providers.llm_fallback == "none"  # no silent cloud fallback in local mode


def test_a1111_local_image_server(server):
    url, h = server
    buf = io.BytesIO()
    Image.new("RGB", (576, 1024), (200, 100, 50)).save(buf, "PNG")
    h.routes[("POST", "/sdapi/v1/txt2img")] = lambda b: (200, {"images": [base64.b64encode(buf.getvalue()).decode()]})
    r = images.A1111Images(url, 8, "AI-generated (local SDXL, CreativeML Open RAIL++-M)").generate("a cafe", 1080, 1920, 7)
    assert r.image.size == (576, 1024) and r.license.startswith("AI-generated")
    assert h.seen[-1][1]["seed"] == 7 and h.seen[-1][1]["width"] < h.seen[-1][1]["height"]


def test_mflux_missing_falls_back_to_procedural(monkeypatch):
    import shutil
    from shortsengine.config import update_settings
    monkeypatch.setattr(shutil, "which", lambda name: None)
    update_settings({"providers": {"image": "mflux", "image_fallback": "procedural"}})
    r = images.generate_with_fallback("medium two-shot in airplane", 1080, 1920, 3)
    assert r.source == "procedural"


@pytest.mark.skipif(not os.environ.get("KOKORO_MODEL_DIR"), reason="set KOKORO_MODEL_DIR to run real Kokoro synthesis")
def test_kokoro_real_synthesis(tmp_path):
    from shortsengine.providers.tts import KokoroTTS
    r = KokoroTTS().synth("You really think you belong here?", "am_michael", tmp_path, "x", rate_pct=5)
    assert 0.8 < r.duration < 5 and len(r.words) == 6 and r.words[0].start < r.words[-1].end <= r.duration
