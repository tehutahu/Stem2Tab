# API 仕様

## 概要

本ドキュメントでは、Stem2Tab バックエンドの REST API エンドポイントを定義します。

## ベース URL

```
http://localhost:8000/api/v1
```

## 認証

Phase 1 では認証なし。Phase 3 で API キーまたは JWT 認証を追加予定。

---

## エンドポイント一覧

| メソッド | パス | 説明 |
|:---|:---|:---|
| `POST` | `/jobs` | ジョブ作成（音源アップロード） |
| `GET` | `/jobs/{job_id}` | ジョブ状態取得 |
| `DELETE` | `/jobs/{job_id}` | ジョブキャンセル (Phase 3) |
| `GET` | `/files/{job_id}` | 成果物ダウンロード |

---

## POST /jobs

音源ファイルをアップロードし、変換ジョブを開始します。

### リクエスト

**Content-Type**: `multipart/form-data`

| パラメータ | 型 | 必須 | 説明 |
|:---|:---|:---|:---|
| `file` | File | Yes | 音源ファイル (mp3, wav, m4a, ogg, flac) |
| `strings` | int | No | ベースの弦数 (デフォルト: 4) |
| `tuning` | string | No | チューニング (デフォルト: "standard") |

### レスポンス

**Status**: `202 Accepted`

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### エラー

| Status | 説明 |
|:---|:---|
| `400` | 不正なファイル形式 |
| `413` | ファイルサイズ超過 (50MB 以上) |
| `500` | サーバーエラー |

```json
{
  "detail": "Unsupported file format. Allowed: mp3, wav, m4a, ogg, flac"
}
```

---

## GET /jobs/{job_id}

ジョブの状態と成果物一覧を取得します。

### パスパラメータ

| パラメータ | 型 | 説明 |
|:---|:---|:---|
| `job_id` | string (UUID) | ジョブID |

### レスポンス

**Status**: `200 OK`

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "SUCCESS",
  "progress": 100,
  "created_at": "2024-01-01T12:00:00Z",
  "updated_at": "2024-01-01T12:05:00Z",
  "files": [
    "bass.wav",
    "bass.mid",
    "bass.gp5"
  ],
  "error": null
}
```

### ステータス値

| 値 | 説明 |
|:---|:---|
| `PENDING` | キューに登録済み、未開始 |
| `STARTED` | 処理中 |
| `SUCCESS` | 完了 |
| `FAILURE` | エラー終了 |
| `RETRY` | リトライ中 (Phase 3) |
| `REVOKED` | キャンセル済み (Phase 3) |

### エラー

| Status | 説明 |
|:---|:---|
| `404` | ジョブが見つからない |

---

## DELETE /jobs/{job_id}

> [!NOTE]
> Phase 3 で実装予定

ジョブをキャンセルします。

### レスポンス

**Status**: `204 No Content`

---

## GET /files/{job_id}

成果物ファイルをダウンロードします。

### パスパラメータ

| パラメータ | 型 | 説明 |
|:---|:---|:---|
| `job_id` | string (UUID) | ジョブID |

### クエリパラメータ

| パラメータ | 型 | 必須 | 説明 |
|:---|:---|:---|:---|
| `name` | string | Yes | ファイル名 (例: `bass.gp5`) |

### レスポンス

**Status**: `200 OK`

**Content-Type**: ファイルに応じた MIME タイプ

| 拡張子 | MIME タイプ |
|:---|:---|
| `.wav` | `audio/wav` |
| `.mid` | `audio/midi` |
| `.gp5` | `application/octet-stream` |
| `.musicxml` | `application/vnd.recordare.musicxml+xml` |

### エラー

| Status | 説明 |
|:---|:---|
| `404` | ファイルが見つからない |
| `400` | `name` パラメータが未指定 |

---

## スキーマ定義

```python
# backend/app/schemas.py
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum

class JobStatus(str, Enum):
    PENDING = "PENDING"
    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    RETRY = "RETRY"
    REVOKED = "REVOKED"

class JobCreateResponse(BaseModel):
    job_id: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = 0
    created_at: datetime
    updated_at: datetime
    files: List[str] = []
    error: Optional[str] = None

class ErrorResponse(BaseModel):
    detail: str
```

---

## 使用例

### cURL

```bash
# ジョブ作成
curl -X POST http://localhost:8000/api/v1/jobs \
  -F "file=@song.mp3" \
  -F "strings=4"

# ステータス確認
curl http://localhost:8000/api/v1/jobs/550e8400-e29b-41d4-a716-446655440000

# ファイルダウンロード
curl -O http://localhost:8000/api/v1/files/550e8400-e29b-41d4-a716-446655440000?name=bass.gp5
```

### JavaScript (Fetch)

```javascript
// ジョブ作成
const formData = new FormData();
formData.append('file', audioFile);

const response = await fetch('/api/v1/jobs', {
  method: 'POST',
  body: formData
});
const { job_id } = await response.json();

// ポーリング
const pollStatus = async () => {
  const res = await fetch(`/api/v1/jobs/${job_id}`);
  const data = await res.json();

  if (data.status === 'SUCCESS') {
    // ダウンロード可能
    return data.files;
  } else if (data.status === 'FAILURE') {
    throw new Error(data.error);
  } else {
    // 継続ポーリング
    setTimeout(pollStatus, 2000);
  }
};
```
