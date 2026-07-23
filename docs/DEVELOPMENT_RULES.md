# 開発・テスト・コーディングルール

本ドキュメントは、AIエージェント（Cursor, GitHub Copilot等）および開発者がStem2Tabプロジェクトで従うべきルールを定義します。

---

## 1. プロジェクト構造

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
│   │   ├── evaluation/        # ベンチマーク、共通ノート、評価指標
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
└── AGENTS.md                  # AIエージェント用ルール
```

> [!NOTE]
> Docker Compose の詳細設定は [INFRASTRUCTURE.md](INFRASTRUCTURE.md) を参照

---

## 2. 技術スタック

### バックエンド

| 項目 | 技術 | 備考 |
|:---|:---|:---|
| 言語 | Python 3.11+ | 型ヒント必須 |
| パッケージ管理 | **uv** | pip は使用しない |
| Web Framework | FastAPI | Pydantic v2 |
| タスクキュー | Celery + Redis | |
| 音源分離 | **Demucs** | PyTorch バックエンド |
| 採譜ベースライン | Basic Pitch (ONNX) | TensorFlow 依存なし。Issue #1で他方式と比較 |
| Tab生成 | PyGuitarPro | GP3-5 のみ対応 |
| MusicXML | music21 | |

### フロントエンド

| 項目 | 技術 |
|:---|:---|
| 言語 | TypeScript |
| Framework | React 18 |
| ビルドツール | Vite |
| 譜面表示 | AlphaTab |
| 状態管理 | Zustand (推奨) |

---

## Basic Pitch インストールポリシー (ONNX 専用)

- **pyproject.toml / uv.lock に Basic Pitch を含めない**（TensorFlow 依存を避けるため）。
- 代わりに `backend/scripts/install_basic_pitch.sh` を実行して **ONNX専用 (--no-deps)** で導入する。
- Docker ビルド・ローカル開発とも同一スクリプトを使用する。
- TensorFlow を追加導入しないこと。

```bash
cd backend
uv sync
./scripts/install_basic_pitch.sh
```

---

## 3. コーディング規約

### Python

```python
# ✅ 良い例
from pathlib import Path
from typing import Optional

def process_audio(input_path: Path, output_dir: Path) -> Optional[Path]:
    """音源を処理し、結果のパスを返す。失敗時はNone。"""
    ...

# ❌ 悪い例
def process_audio(input_path, output_dir):
    ...
```

| ルール | 説明 |
|:---|:---|
| **型ヒント必須** | 関数の引数と戻り値には必ず型ヒントをつける |
| **Path オブジェクト** | ファイルパスは `str` ではなく `pathlib.Path` を使用 |
| **docstring** | 公開関数には必ず docstring をつける |
| **構造化ログ** | `print()` ではなく `structlog` を使用、`job_id` を含める |
| **例外処理** | 例外は握りつぶさない、適切にログを記録して再送出 |

### TypeScript

```typescript
// ✅ 良い例
interface FretboardProps {
  activeNotes: FretboardNote[];
  strings: number;
}

function Fretboard({ activeNotes, strings = 4 }: FretboardProps): JSX.Element {
  ...
}

// ❌ 悪い例
function Fretboard(props: any) {
  ...
}
```

| ルール | 説明 |
|:---|:---|
| **型定義必須** | `any` は原則使用禁止 |
| **関数コンポーネント** | クラスコンポーネントは使用しない |
| **Props 型定義** | interface で明示的に定義 |

---

## 4. モジュール分離の原則

> [!IMPORTANT]
> 分離、採譜、ノート後処理、リズム量子化、記譜を疎結合にすること

- 分離器と採譜器はアダプター経由で交換可能にする。
- ベンチマークCLIはWeb APIやCeleryを経由せず独立して実行できるようにする。
- Basic Pitchの生MIDIをScore MIDIとして扱わない。
- Web workerへ候補方式を接続するのは、同じ入力で比較して採用方式を決めた後にする。
- フロントエンドの表示・編集ロジックをバックエンドの採譜処理へ混入させない。

---

## 5. API 設計ルール

| ルール | 説明 |
|:---|:---|
| **バージョニング** | `/api/v1/...` のようにバージョンを含める |
| **命名** | リソース名は複数形 (`/jobs`, `/files`) |
| **HTTPメソッド** | GET=取得, POST=作成, DELETE=削除 |
| **ステータスコード** | 成功=200/201/202, エラー=4xx/5xx |
| **エラーレスポンス** | `{"detail": "エラー内容"}` |

---

## 6. テストルール

### 単体テスト（コンテナ内で実行）

```bash
# バックエンド
docker compose exec api uv run pytest tests/unit -v

# フロントエンド（Reactテストは NODE_ENV=test を指定）
docker compose exec -e NODE_ENV=test web npm test -- --run
```

| ルール | 説明 |
|:---|:---|
| **カバレッジ目標** | 80% 以上 |
| **モック活用** | 外部サービス（Demucs, Basic Pitch）はモック化 |
| **フィクスチャ** | テストデータは `fixtures/` に配置 |

### 統合テスト（コンテナ内）

```bash
docker compose exec api uv run pytest tests/integration -v
```

### E2E テスト

```bash
# E2E（Playwright を web コンテナ内で実行）
docker compose up -d
docker compose run --rm web npx playwright test
docker compose down
```

---

## 7. Git ルール

### ブランチ戦略

| ブランチ | 用途 |
|:---|:---|
| `main` | 本番リリース可能な状態 |
| `develop` | 開発ブランチ |
| `feature/*` | 新機能開発 |
| `fix/*` | バグ修正 |

### コミットメッセージ

```
<type>: <subject>

<body>
```

| type | 説明 |
|:---|:---|
| `feat` | 新機能 |
| `fix` | バグ修正 |
| `docs` | ドキュメント |
| `refactor` | リファクタリング |
| `test` | テスト追加 |
| `chore` | ビルド、依存関係 |

例:
```
feat: 演奏デモモードの基本実装

- AlphaTabの再生カーソル統合
- フレットボードコンポーネント追加
```

---

## 8. 環境構築手順

### バックエンド

```bash
cd backend

# uv インストール
curl -LsSf https://astral.sh/uv/install.sh | sh

# 依存関係インストール
uv sync

# 開発サーバー起動
uv run uvicorn src.api.main:app --reload
```

### フロントエンド

```bash
cd frontend

# 依存関係インストール
npm install

# 開発サーバー起動
npm run dev
```

### Docker

```bash
# 全サービス起動 (GPU)
docker compose up --build

# CPU のみ
docker compose -f docker-compose.cpu.yml up --build

# テスト一括（コンテナ内）
make test-all
```

---

## 9. AIエージェント向け注意事項

> [!CAUTION]
> **以下の点に特に注意してください**

1. **現行ベースラインは Demucs + Basic Pitch**。評価基盤ではアダプターで交換可能に保つ
2. **pip は使用しない** → uv を使用
3. **TensorFlow は使用しない** → Basic Pitch は ONNX バックエンド
4. **正解MIDIは現時点で存在しない** → `--reference`なしの聴感比較を既定とする
5. **GPU が標準** → CPU はフォールバック
6. **GP6/GP7 は PyGuitarPro で非対応** → GP5 を出力
7. **モジュール分離を維持** → 分離/採譜/後処理/量子化/記譜を疎結合にする

### 関連ドキュメント参照

| 目的 | ドキュメント |
|:---|:---|
| システム全体像 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| データフロー | [DATAFLOW.md](DATAFLOW.md) |
| 機能仕様 | [FEATURES.md](FEATURES.md) |
| 依存関係 | [DEPENDENCIES.md](DEPENDENCIES.md) |
| API仕様 | [API.md](API.md) |
| テスト | [TESTING.md](TESTING.md) |
| 演奏デモ | [DEMO_MODE.md](DEMO_MODE.md) |
| 技術調査 | [TECH_RESEARCH.md](TECH_RESEARCH.md) |
| **Docker Compose** | [INFRASTRUCTURE.md](INFRASTRUCTURE.md) |
