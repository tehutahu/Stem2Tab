#!/usr/bin/env bash
set -euo pipefail

# Install Basic Pitch in ONNX-only mode (no TensorFlow deps).
# Must run from backend root where pyproject/uv.lock exist.

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required but not found. Install uv first." >&2
  exit 1
fi

UV_PY="${UV_PYTHON:-3.11}"

echo "[basic-pitch] Installing basic-pitch==0.4.0 with --no-deps (ONNX backend only)"
uv pip install --python "${UV_PY}" --no-deps basic-pitch==0.4.0

