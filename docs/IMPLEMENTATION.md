# 実装計画

> [!IMPORTANT]
> 実装優先順位は [ROADMAP.md](ROADMAP.md) と
> [Issue #1](https://github.com/tehutahu/Stem2Tab/issues/1) を正本とします。
> Web UIや運用機能を先に拡張せず、採譜品質を比較できる評価基盤を先に構築します。

## 旧Web MVP計画（履歴）

```mermaid
gantt
    title 開発ロードマップ
    dateFormat  YYYY-MM-DD
    section Phase 1: MVP
    Backend API/Worker             :a1, 2024-01-01, 14d
    Frontend Upload/Progress       :a2, after a1, 7d
    AlphaTab 統合                  :a3, after a2, 7d
    section Phase 2: 機能拡張
    MusicXML 出力                  :b1, after a3, 7d
    Tab割当アルゴリズム改善        :b2, after b1, 14d
    section Phase 3: 運用強化
    S3/監視/クリーンアップ         :c1, after b2, 14d
```

## 依存関係と環境

### パッケージマネージャ

**`uv`** を使用して依存関係を管理します。

```bash
# プロジェクト初期化
uv init

# 依存関係のインストール
uv sync

# lockfileの生成
uv lock
```

### Python 依存関係

```toml
# backend/pyproject.toml
[project]
name = "stem2tab"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "celery[redis]>=5.3.0",
    "redis>=5.0.0",
    "demucs>=4.0.0",
    "pyguitarpro>=0.10.0",
    "music21>=9.0.0",
    "librosa>=0.10.0",
    "soundfile>=0.12.0",
    "onnxruntime>=1.19.2",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "structlog>=24.1.0",
]

[tool.uv]
dev-dependencies = ["pytest>=8.0.0", "pytest-asyncio>=0.23.0", "httpx>=0.27.0"]
```

### システム依存関係 (Dockerfile)

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
```

### Frontend 依存関係

```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "@coderline/alphatab": "^1.3.0"
  },
  "devDependencies": {
    "typescript": "^5.3.0",
    "vite": "^7.2.6",
    "@vitejs/plugin-react": "^5.1.1",
    "vitest": "^4.0.15"
  }
}
```

## 環境変数

| 変数名 | 説明 | 必須 | デフォルト |
|:---|:---|:---|:---|
| `FILE_BUCKET_PATH` | 成果物の保存先ディレクトリ | Yes | `/data` |
| `CELERY_BROKER_URL` | Redis ブローカーURL | Yes | `redis://redis:6379/0` |
| `DEMUCS_MODEL` | Demucsモデル名 | No | `htdemucs` |
| `API_PORT` | APIの待受ポート | No | `8000` |
| `WEB_PORT` | web preview の待受ポート | No | `4173` |
| `LOG_LEVEL` | ログレベル | No | `info` |
| `DEMUCS_CACHE_SUBDIR` | Demucsモデルキャッシュ相対パス (`FILE_BUCKET_PATH` 配下) | No | `cache/demucs` |

- `FILE_BUCKET_PATH` の `/data` はコンテナ内パス。変更したい場合は、`docker-compose*.yml` のボリュームマウント先と合わせて設定すること（例: `FILE_BUCKET_PATH=/workspace/data` とし、compose 側も `/workspace/data` をマウントする）。

### モデル取得ポリシー

- Demucs は **起動時オンデマンドDL**。未取得の場合のみ `DEMUCS_CACHEDIR`（デフォルト `/data/cache/demucs`）にダウンロードし、ボリュームで永続化。

## CPU/GPU ポリシー

| 環境 | 設定 | 備考 |
|:---|:---|:---|
| GPU (標準) | `deploy.resources.devices` で `nvidia` 指定 | ホストに NVIDIA Container Toolkit が必要 |
| CPU (フォールバック) | GPU設定なし | 動作は遅いが必ず動作する |

```yaml
# docker-compose.yml (GPU有効時)
worker:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```

- GPU 版: `docker-compose.yml` / CPU 版: `docker-compose.cpu.yml` を用意。`./data:/data` を共有し、API/worker が同一キャッシュを再利用。

## ストレージポリシー

### ローカルストレージ (Phase 1)

- 保存先: `/data/{job_id}/`
- ボリュームマウント: `./data:/data`

### S3 ストレージ (Phase 3)

- キー構造: `{job_id}/out/{filename}`
- Presigned URL で期限付き配信

## ロギングと監視

### 構造化ログ

```python
import structlog

logger = structlog.get_logger()
logger.info("job_started", job_id=job_id, input_file=filename)
logger.info("demucs_complete", job_id=job_id, duration_sec=elapsed)
```

### メトリクス (Phase 3)

- キュー長
- 処理時間分布 (p50, p95, p99)
- エラー率
- GPU 利用率

## 現在の実装順序

1. **評価契約を固定**
   - 共通ノートイベントモデル
   - MIDI/CSV/JSON入出力
   - 設定と成果物のディレクトリ規約
2. **ベンチマークCLIを実装**
   - 手元の数曲を引数で指定
   - Basic Pitchの複数パラメータを同じ入力で比較
   - 評価値と比較レポートを保存
3. **評価データ方針を相談して決定**
   - 曲数、区間長、正解MIDI/TABの作成方法
   - 公開データセットの取得量、ライセンス、派生物の保存範囲
4. **候補方式を追加**
   - pYIN等の古典ベースライン
   - 合意したPESTO/TorchCREPE等のアダプター
   - 新しい本番依存は追加前に確認
5. **リズムと楽譜表現を分離**
   - beat/downbeat、Performance MIDI、Score MIDI、量子化
6. **TABと編集UIへ接続**
   - DP運指最適化、MusicXML/GP5、修正操作の記録

大規模公開データセットのダウンロードとWeb UIの追加開発は、最初のベンチマーク結果を確認するまで保留します。
