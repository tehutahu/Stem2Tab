# インフラストラクチャ

## 概要

本プロジェクトはDocker Composeを使用してローカル開発環境およびデプロイを行います。

## ディレクトリ構造

```
Stem2Tab/
├── docker-compose.yml          # GPU版（標準）
├── docker-compose.cpu.yml      # CPU版（フォールバック）
├── .env.example                # 環境変数テンプレート
├── backend/
│   └── Dockerfile              # バックエンド用
├── frontend/
│   └── Dockerfile              # フロントエンド用
└── infrastructure/             # 追加設定（オプション）
    └── k8s/                    # Kubernetes マニフェスト（将来）
```

---

## Docker Compose 設定

### docker-compose.yml (GPU版 - 標準)

主要ポイント:

- `version: "3.9"`、共通ビルド定義 (`x-backend-build`) で PyTorch CUDA12 イメージを利用。
- `api`/`worker` で `BASIC_PITCH_MODEL_SERIALIZATION=onnx` を明示し、Demucs キャッシュ (`DEMUCS_CACHEDIR`/`TORCH_HOME`) を `/data/cache/demucs` に固定。GPU イメージでは `onnxruntime-gpu` (1.20.1) をインストール。
- `worker` に Celery ping ベースのヘルスチェックを付与。
- GPU 予約は `deploy.resources.devices` にて `nvidia` を指定。

```yaml
version: "3.9"

x-backend-build: &backend-build
  context: ./backend
  dockerfile: Dockerfile
  args:
    BASE_IMAGE: pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime
x-backend-image: &backend-image stem2tab-backend:latest

services:
  redis:
    image: redis:7.2-alpine
    command: ["redis-server", "--save", "", "--appendonly", "no"]
    ports: ["6379:6379"]
    healthcheck: ["CMD", "redis-cli", "ping"]

  api:
    image: *backend-image
    build: *backend-build
    env_file: .env
    environment:
      - FILE_BUCKET_PATH=${FILE_BUCKET_PATH:-/data}
      - CELERY_BROKER_URL=${CELERY_BROKER_URL:-redis://redis:6379/0}
      - DEMUCS_MODEL=${DEMUCS_MODEL:-htdemucs}
      - BASIC_PITCH_MODEL_SERIALIZATION=${BASIC_PITCH_MODEL_SERIALIZATION:-onnx}
      - API_PORT=${API_PORT:-8000}
      - DEMUCS_CACHEDIR=${FILE_BUCKET_PATH:-/data}/cache/demucs
      - TORCH_HOME=${FILE_BUCKET_PATH:-/data}/cache/demucs
    command: ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "${API_PORT:-8000}"]
    volumes:
      - ./data:${FILE_BUCKET_PATH:-/data}
      - ./backend/src:/app/src
      - ./backend/tests:/app/tests
      - torch-cache:/root/.cache/torch
      - uv-cache:/root/.cache/uv
    ports: ["${API_PORT:-8000}:${API_PORT:-8000}"]
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:${API_PORT:-8000}/health"]

  worker:
    image: *backend-image
    env_file: .env
    environment:
      - FILE_BUCKET_PATH=${FILE_BUCKET_PATH:-/data}
      - CELERY_BROKER_URL=${CELERY_BROKER_URL:-redis://redis:6379/0}
      - DEMUCS_MODEL=${DEMUCS_MODEL:-htdemucs}
      - BASIC_PITCH_MODEL_SERIALIZATION=${BASIC_PITCH_MODEL_SERIALIZATION:-onnx}
      - DEMUCS_CACHEDIR=${FILE_BUCKET_PATH:-/data}/cache/demucs
      - TORCH_HOME=${FILE_BUCKET_PATH:-/data}/cache/demucs
      - PYTHONPATH=/app/src
      - NVIDIA_VISIBLE_DEVICES=all
    command: ["celery", "-A", "src.worker.app", "worker", "-l", "info"]
    volumes:
      - ./data:${FILE_BUCKET_PATH:-/data}
      - ./backend/src:/app/src
      - ./backend/tests:/app/tests
      - torch-cache:/root/.cache/torch
      - uv-cache:/root/.cache/uv
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "celery -A src.worker.app inspect ping -d celery@$(hostname)"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 20s
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  web:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    env_file: .env
    command: ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "${WEB_PORT:-4173}"]
    ports: ["${WEB_PORT:-4173}:${WEB_PORT:-4173}"]
    depends_on:
      api:
        condition: service_started

volumes:
  torch-cache:
  uv-cache:
```

### docker-compose.cpu.yml (CPU版 - フォールバック)

- `BASE_IMAGE` を `pytorch/pytorch:2.3.1-cpu` に変更した以外は GPU 版と同等。
- GPU 予約設定なし（CPU 動作）。
- 同じヘルスチェック／環境変数 (`BASIC_PITCH_MODEL_SERIALIZATION=onnx`) を維持。

---

## Dockerfile

### backend/Dockerfile

```dockerfile
FROM python:3.11-slim

# システム依存関係
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    git \
    && rm -rf /var/lib/apt/lists/*

# uv インストール
RUN pip install --no-cache-dir uv

WORKDIR /app

# 依存関係のインストール
COPY backend/pyproject.toml backend/uv.lock* ./
RUN uv sync --frozen

# アプリケーションコードのコピー
COPY backend/ .

# モデルの事前ダウンロード (オプション)
RUN uv run python -c "import demucs.pretrained; demucs.pretrained.get_model('htdemucs')"

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### frontend/Dockerfile

```dockerfile
FROM node:20-slim

WORKDIR /app

# 依存関係のインストール
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

# アプリケーションコードのコピー
COPY frontend/ .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
```

---

## 環境変数

### .env.example

```bash
FILE_BUCKET_PATH=/data
CELERY_BROKER_URL=redis://redis:6379/0
DEMUCS_MODEL=htdemucs
BASIC_PITCH_MODEL_SERIALIZATION=onnx
API_PORT=8000
WEB_PORT=4173
LOG_LEVEL=info
DEMUCS_CACHE_SUBDIR=cache/demucs
DEMUCS_CACHEDIR=/data/cache/demucs
TORCH_HOME=/data/cache/demucs
```

---

## ホスト前提とセットアップ (WSL/Ubuntu)

- Docker 29+ / Docker Compose v2
- Python 3.11+（ホストで uv を使う場合）
- Node.js 18+（推奨20、フロントをホスト実行する場合）
- GPU利用時: NVIDIA ドライバ 525+ と NVIDIA Container Toolkit

### インストール例

```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Node.js は nvm 等で 18/20 系を導入することを推奨
```

### 確認コマンド

```bash
python3 --version
uv --version
node -v && npm -v
docker --version && docker compose version
nvidia-smi  # GPU マシンのみ
```

---

## 起動コマンド

```bash
# GPU版（標準）
docker compose up --build

# GPU版（バックグラウンド）
docker compose up -d --build

# CPU版
docker compose -f docker-compose.cpu.yml up --build

# ログ確認
docker compose logs -f worker

# 停止
docker compose down

# ボリュームも削除
docker compose down -v
```

### テスト実行（コンテナ内）

```bash
# バックエンド単体
docker compose run --rm api uv run pytest tests/unit -v

# バックエンド統合（事前に依存を起動）
docker compose up -d redis worker api
docker compose run --rm api uv run pytest tests/integration -v
docker compose down

# フロントエンド単体
docker compose run --rm web npm test

# フロントエンドE2E（Playwright）
docker compose up -d
docker compose run --rm web npx playwright test
docker compose down
```

---

## GPU セットアップ (NVIDIA)

### ホスト要件

1. NVIDIA ドライバー (525+)
2. NVIDIA Container Toolkit

```bash
# Ubuntu での NVIDIA Container Toolkit インストール
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | \
  sudo tee /etc/apt/sources.list.d/nvidia-docker.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker

# 確認
docker run --rm --gpus all nvidia/cuda:11.8-base-ubuntu22.04 nvidia-smi
```

---

## ポート一覧

| サービス | ポート | 用途 |
|:---|:---|:---|
| api | 8000 | REST API |
| web | 5173 | フロントエンド (Vite dev server) |
| redis | 6379 | Celery ブローカー |

---

## ヘルスチェック

```bash
# API
curl http://localhost:8000/health

# Redis
docker compose exec redis redis-cli ping

# Worker
docker compose exec worker celery -A src.core.celery_app inspect ping
```
