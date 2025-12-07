ARG BASE_IMAGE=pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON=3.11

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libsndfile1 curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv (project dependency manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ln -s /root/.local/bin/uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./

RUN uv python install 3.11 && \
    uv sync --locked --no-dev --python 3.11 && \
    uv run --python 3.11 python -V

COPY src ./src
COPY tests ./tests

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH=/app/src

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

