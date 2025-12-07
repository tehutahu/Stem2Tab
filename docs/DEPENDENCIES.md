# 依存関係仕様

## 概要

本プロジェクトでは複数の外部ライブラリと機械学習モデルを使用します。
このドキュメントでは、依存関係の管理方法、バージョン固定戦略、既知の互換性問題について記載します。

## パッケージマネージャ

**`uv`** を使用します。

```bash
# インストール
curl -LsSf https://astral.sh/uv/install.sh | sh

# プロジェクトの依存関係をインストール
cd backend
uv sync

# lockfile の更新
uv lock
```

## Python 依存関係

### コアライブラリ

| パッケージ | バージョン | 用途 | 公式ドキュメント |
|:---|:---|:---|:---|
| `demucs` | `>=4.0.0` | 音源分離 (Bass stem 抽出) | [GitHub](https://github.com/facebookresearch/demucs) |
| `onnxruntime` | `>=1.19.2` | Audio-to-MIDI 変換用ランタイム (Basic Pitch ONNX) | [GitHub](https://github.com/microsoft/onnxruntime) |
| `pyguitarpro` | `>=0.10.0` | Guitar Pro 3-5 読み書き | [Docs](https://pyguitarpro.readthedocs.io/) |
| `music21` | `>=9.0.0` | MusicXML 出力 | [Docs](https://web.mit.edu/music21/doc/) |
| `librosa` | `>=0.10.0` | 音声処理ユーティリティ | [Docs](https://librosa.org/) |
| `soundfile` | `>=0.12.0` | 音声ファイル I/O | [GitHub](https://github.com/bastibe/python-soundfile) |

### Web フレームワーク

| パッケージ | バージョン | 用途 |
|:---|:---|:---|
| `fastapi` | `>=0.109.0` | REST API |
| `uvicorn[standard]` | `>=0.27.0` | ASGI サーバー |
| `pydantic` | `>=2.0.0` | データバリデーション |

### 非同期処理

| パッケージ | バージョン | 用途 |
|:---|:---|:---|
| `celery[redis]` | `>=5.3.0` | タスクキュー |
| `redis` | `>=5.0.0` | Celery ブローカー/バックエンド |

## システム依存関係

Docker イメージに以下をインストールする必要があります。

| パッケージ | 用途 | 備考 |
|:---|:---|:---|
| `ffmpeg` | Demucs などの音声デコード/変換 | 必須 |
| `libsndfile1` | soundfile の動作に必要 | 必須 |

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
```

## 機械学習モデル

### Demucs モデル

| モデル名 | サイズ | 用途 |
|:---|:---|:---|
| `htdemucs` | ~80MB | **Hybrid Transformer Demucs (推奨)** |
| `htdemucs_ft` | ~80MB | Fine-tuned版 (より高品質) |
| `mdx_extra` | ~80MB | MDX-Net ベース |

**キャッシュ場所**: `~/.cache/torch/hub/` (PyTorch Hub経由でダウンロード)

**ダウンロード戦略**:
- **ランタイム**: 初回実行時に自動ダウンロード (PyTorch Hub)
- **Dockerfile**: 事前ダウンロードでイメージに含める

```dockerfile
# ビルド時にモデルをダウンロード
RUN python -c "import demucs.pretrained; demucs.pretrained.get_model('htdemucs')"
```

### Basic Pitch (ONNX) モデル

- ONNX 形式のモデルを使用（TensorFlow 依存なし）。実装時に ONNX ファイルを配置するか、初回起動時にダウンロードする。

## 既知の互換性問題

> [!NOTE]
> **TensorFlow 依存の排除**
>
> 現行構成は **Demucs (PyTorch)** と **Basic Pitch (ONNX)** のみを使用し、TensorFlow には依存しません。
> **PyTorch と ONNX は共存可能**であり、依存関係の競合リスクは低いです。

## Frontend 依存関係

### npm パッケージ

| パッケージ | バージョン | 用途 |
|:---|:---|:---|
| `react` | `^18.2.0` | UI フレームワーク |
| `react-dom` | `^18.2.0` | React DOM レンダラー |
| `@coderline/alphatab` | `^1.3.0` | 譜面表示/再生 |

### AlphaTab の追加要件

AlphaTab で音声再生を行うには **SoundFont** ファイルが必要です。

| ファイル | サイズ | 説明 |
|:---|:---|:---|
| `sonivox.sf2` | ~3.5MB | 標準的な GM SoundFont |
| または任意の SF2 | - | カスタムサウンド |

**配置場所**: `frontend/public/soundfonts/`

```typescript
// AlphaTab 初期化時に指定
new alphaTab.AlphaTabApi(element, {
  player: {
    enablePlayer: true,
    soundFont: '/soundfonts/sonivox.sf2'
  }
});
```

## バージョン固定戦略

1. **開発時**: `uv lock` で `uv.lock` ファイルを生成
2. **CI/CD**: `uv sync --locked` で lockfile に従ってインストール
3. **依存関係更新**: 定期的に `uv lock --upgrade` を実行し、テストで動作確認

## リファレンス

- [Basic Pitch README](https://github.com/spotify/basic-pitch)
- [PyGuitarPro Documentation](https://pyguitarpro.readthedocs.io/)
- [AlphaTab Documentation](https://alphatab.net/docs/introduction/)
- [uv Documentation](https://docs.astral.sh/uv/)
