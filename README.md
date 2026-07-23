# Stem2Tab

音源ファイルからベースラインを抽出し、修正可能なMIDIとTab譜（Guitar Pro / MusicXML）を生成するWebアプリケーション。

## 概要

```mermaid
flowchart LR
    A[原曲] --> B[音源分離]
    A --> C[拍・ダウンビート推定]
    B --> D[Bass Stem]
    D --> E[F0・オンセット・ノート分割]
    E --> F[Performance MIDI]
    C --> G[楽譜量子化]
    F --> G
    G --> H[Score MIDI]
    H --> I[弦・フレット割当]
    I --> J[GP5 / MusicXML]
    J --> K[AlphaTab で確認・修正]
```

既存のDemucs→Basic Pitch→GP5/MusicXML処理はWebフローを検証するためのベースラインです。
現在の最優先事項は採譜品質を測定・比較できる評価基盤であり、実装順序は
[開発ロードマップ](docs/ROADMAP.md) と [Issue #1](https://github.com/tehutahu/Stem2Tab/issues/1)
を正本とします。

## プロジェクト構造

```
Stem2Tab/
├── docker-compose.yml         # GPU版（標準）
├── docker-compose.cpu.yml     # CPU版
├── .env.example               # 環境変数テンプレート
├── backend/                   # Python バックエンド
│   ├── Dockerfile
│   ├── pyproject.toml         # uv 用
│   ├── src/
│   │   ├── api/               # FastAPI ルート
│   │   ├── pipelines/         # 現行の分離、採譜、Tab出力
│   │   ├── worker/            # Celeryタスク
│   │   └── core/              # 設定、ログ
│   └── tests/
├── frontend/                  # React フロントエンド
│   ├── Dockerfile
│   ├── package.json
│   └── src/
│       ├── components/        # AlphaTab, Fretboard 等
│       ├── hooks/
│       └── pages/
├── docs/                      # ドキュメント
├── data/                      # 成果物保存先 (gitignore)
└── .agent/                    # AIエージェント用ルール
    └── rules/
```

## 技術スタック

| レイヤー | 技術 |
|:---|:---|
| **音源分離** | [Demucs](https://github.com/facebookresearch/demucs) (Hybrid Transformer v4) |
| **採譜ベースライン** | [Basic Pitch](https://github.com/spotify/basic-pitch) (ONNX)。今後F0方式と比較 |
| **Tab生成** | [PyGuitarPro](https://pyguitarpro.readthedocs.io/), [music21](https://web.mit.edu/music21/doc/) |
| **バックエンド** | FastAPI + Celery + Redis |
| **フロントエンド** | React + Vite + [AlphaTab](https://alphatab.net/) |
| **パッケージ管理** | [uv](https://docs.astral.sh/uv/) |

## 前提環境と確認方法

- OS: Ubuntu 22.04+/24.04（WSL可）
- Docker: 29+（`docker --version`）
- Docker Compose: v2（`docker compose version`）
- Python: 3.11+（`python3 --version`） ※パッケージは必ず uv を使用
- uv: `uv --version`（未導入なら `curl -LsSf https://astral.sh/uv/install.sh | sh`）
- Node.js: 20.19+ または 22.12+（`node -v`）, npm: `npm -v`
- Git: `git --version`
- GPU 利用時: NVIDIA ドライバ + NVIDIA Container Toolkit（`nvidia-smi` がホストで動作し、下記コンテナ確認が通ること）

動作確認コマンド例:

```bash
python3 --version
uv --version
node -v && npm -v
docker --version && docker compose version
nvidia-smi  # GPUマシンのみ
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi  # GPUパススルー確認
```

## クイックスタート

```bash
# リポジトリをクローン
git clone https://github.com/tehutahu/Stem2Tab.git
cd Stem2Tab

# 環境変数の設定
cp .env.example .env

# Docker Compose で起動 (GPU版)
docker compose up --build

# CPUのみの場合
docker compose -f docker-compose.cpu.yml up --build

# ブラウザでアクセス
open http://localhost:5173
```

> [!NOTE]
> GPUを使用する場合は NVIDIA Container Toolkit が必要です。
> 詳細は [INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) を参照してください。

## ドキュメント

| ドキュメント | 内容 |
|:---|:---|
| [ROADMAP.md](docs/ROADMAP.md) | **現在の実装優先順位、評価データの判断事項** |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | システム構成、コンポーネント、デプロイ |
| [DATAFLOW.md](docs/DATAFLOW.md) | パイプライン処理、ジョブ状態管理 |
| [FEATURES.md](docs/FEATURES.md) | 機能一覧、Phase別ロードマップ |
| [IMPLEMENTATION.md](docs/IMPLEMENTATION.md) | 実装計画、依存関係、環境変数 |
| [DEPENDENCIES.md](docs/DEPENDENCIES.md) | 外部ライブラリ、モデル管理、互換性 |
| [TESTING.md](docs/TESTING.md) | テスト戦略、CI/CD |
| [DEMO_MODE.md](docs/DEMO_MODE.md) | 演奏デモモード機能仕様 |
| [TECH_RESEARCH.md](docs/TECH_RESEARCH.md) | 技術スタック調査レポート |
| [INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) | **Docker Compose, Dockerfile, 環境変数** |
| [DEVELOPMENT_RULES.md](docs/DEVELOPMENT_RULES.md) | 開発・テスト・コーディングルール |

## 開発

### 環境構築

```bash
# バックエンド
cd backend
uv sync

# フロントエンド
cd frontend
npm install
```

### ローカル実行

```bash
# バックエンド (開発サーバー)
cd backend
uv run uvicorn src.api.main:app --reload

# フロントエンド (開発サーバー)
cd frontend
npm run dev
```

### テスト

```bash
# バックエンド
cd backend
uv run pytest

# フロントエンド
cd frontend
npm test -- --run
```

### npm のプロキシ警告

npm では、環境変数から設定された `npm_config_http_proxy` / `npm_config_https_proxy`
（または `http-proxy` / `https-proxy`）が将来のメジャーバージョンで廃止予定です。
プロキシが必要な環境では、プロキシの標準環境変数 (`HTTP_PROXY`、`HTTPS_PROXY`、
`NO_PROXY`) のみを設定し、npm 固有の変数は削除してください。例えば、ローカルの
シェル設定や CI 設定から `npm_config_http_proxy` と `npm_config_https_proxy` を削除します。
これは開発環境の設定に起因する警告であり、アプリケーションの実行時プロキシ
（`API_PROXY_TARGET`）とは別の設定です。

## ライセンス

MIT License

## 参考リンク

- [Demucs GitHub](https://github.com/facebookresearch/demucs)
- [Basic Pitch GitHub](https://github.com/spotify/basic-pitch)
- [PyGuitarPro Documentation](https://pyguitarpro.readthedocs.io/)
- [AlphaTab Documentation](https://alphatab.net/docs/introduction/)
- [Docker GPU Support](https://docs.docker.com/compose/how-tos/gpu-support/)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
