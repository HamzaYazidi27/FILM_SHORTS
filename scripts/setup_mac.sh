#!/usr/bin/env bash
# One-time setup for a fully LOCAL ShortsEngine on Apple Silicon (no API keys).
#   LLM    : Ollama  (default model qwen2.5:14b, ~9 GB, fits 32 GB unified memory)
#   Images : FLUX.1-schnell via mflux / MLX (Apache-2.0; first run downloads the weights once)
#   Voice  : Kokoro-82M via ONNX (Apache-2.0; ~120 MB download)
# Usage:  bash scripts/setup_mac.sh            (add --warm-flux to pre-download FLUX now)
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${OLLAMA_MODEL:-qwen2.5:14b}"
[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || echo "warning: this script targets macOS on Apple Silicon"
command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }

echo "==> system packages"
brew install python@3.12 ffmpeg ollama

echo "==> python environment (.venv)"
"$(brew --prefix python@3.12)/bin/python3.12" -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[local,dev]"

echo "==> Ollama"
brew services start ollama || (ollama serve >/tmp/ollama.log 2>&1 &)
for i in $(seq 1 30); do curl -s localhost:11434/api/version >/dev/null && break; sleep 1; done
ollama pull "$MODEL"

echo "==> ShortsEngine local profile"
python -m shortsengine init
python -m shortsengine profile local
python - <<PY
from shortsengine.config import update_settings
update_settings({"providers": {"ollama_model": "$MODEL"}})
PY

if [[ "${1:-}" == "--warm-flux" ]]; then
  echo "==> pre-downloading FLUX.1-schnell (large, one time)"
  mflux-generate --model schnell --quantize 4 --steps 2 --width 256 --height 448 --seed 1 \
    --prompt "test" --output /tmp/flux_warmup.png
fi

python -m shortsengine doctor --deep || true
echo
echo "Done. Start the dashboard with:"
echo "  source .venv/bin/activate && python -m shortsengine serve"
echo "Then open http://127.0.0.1:8000"
