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

```yaml
version: "3.9"

services:
  # FastAPI バックエンド
  api:
    build:
      context: .
      dockerfile: backend/Dockerfile
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - FILE_BUCKET_PATH=/data
    volumes:
      - ./data:/data
    depends_on:
      - redis
      - worker
    command: uvicorn src.api.main:app --host 0.0.0.0 --port 8000

  # Celery ワーカー (GPU)
  worker:
    build:
      context: .
      dockerfile: backend/Dockerfile
    env_file: .env
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - FILE_BUCKET_PATH=/data
    volumes:
      - ./data:/data
    depends_on:
      - redis
    command: celery -A src.core.celery_app worker --loglevel=INFO
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  # Redis (Celery ブローカー/バックエンド)
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  # React フロントエンド
  web:
    build:
      context: .
      dockerfile: frontend/Dockerfile
    ports:
      - "5173:5173"
    depends_on:
      - api
    command: npm run dev -- --host 0.0.0.0

volumes:
  redis_data:
```

### docker-compose.cpu.yml (CPU版 - フォールバック)

```yaml
version: "3.9"

services:
  api:
    build:
      context: .
      dockerfile: backend/Dockerfile
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - FILE_BUCKET_PATH=/data
    volumes:
      - ./data:/data
    depends_on:
      - redis
      - worker
    command: uvicorn src.api.main:app --host 0.0.0.0 --port 8000

  # Celery ワーカー (CPU)
  worker:
    build:
      context: .
      dockerfile: backend/Dockerfile
    env_file: .env
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - FILE_BUCKET_PATH=/data
    volumes:
      - ./data:/data
    depends_on:
      - redis
    command: celery -A src.core.celery_app worker --loglevel=INFO
    # GPU設定なし = CPU動作

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  web:
    build:
      context: .
      dockerfile: frontend/Dockerfile
    ports:
      - "5173:5173"
    depends_on:
      - api
    command: npm run dev -- --host 0.0.0.0

volumes:
  redis_data:
```

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
# ストレージ
FILE_BUCKET_PATH=/data

# Celery
CELERY_BROKER_URL=redis://redis:6379/0

# Demucs
DEMUCS_MODEL=htdemucs

# API設定
API_HOST=0.0.0.0
API_PORT=8000

# ログレベル
LOG_LEVEL=INFO
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
