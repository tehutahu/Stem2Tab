ARG BASE_IMAGE=pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime
ARG UV_PYTHON_VERSION=3.11
ARG USE_ONNXRUNTIME_GPU=false
ARG ONNXRUNTIME_GPU_VERSION=1.20.1
FROM ${BASE_IMAGE}
# Re-declare build args for availability after FROM.
ARG UV_PYTHON_VERSION
ARG USE_ONNXRUNTIME_GPU
ARG ONNXRUNTIME_GPU_VERSION

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON=${UV_PYTHON_VERSION} \
    UV_LINK_MODE=copy \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_COLOR=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libsndfile1 curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv (project dependency manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ln -s /root/.local/bin/uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./ 
COPY scripts ./scripts

# Use system Python (3.11 in base image) and cache uv downloads for faster rebuilds.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --python ${UV_PYTHON_VERSION} && \
    if [ "${USE_ONNXRUNTIME_GPU}" = "true" ]; then \
      printf 'y\n' | uv pip uninstall onnxruntime || true && \
      uv pip install --no-deps onnxruntime-gpu==${ONNXRUNTIME_GPU_VERSION}; \
    fi && \
    chmod +x scripts/install_basic_pitch.sh && \
    ./scripts/install_basic_pitch.sh && \
    uv run --python ${UV_PYTHON_VERSION} python -V

COPY src ./src
COPY tests ./tests

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH=/app/src

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

