> 主要ライブラリ仕様の根拠
>
> * **Spleeter**：2/4/5 stems、`ffmpeg`/`libsndfile`必須、CLI/ライブラリ両対応。([GitHub][1])
> * **Basic Pitch**：CLIあり・`predict/predict_and_save` APIあり、入力は `.mp3/.m4a/.wav/.ogg/.flac` 等（librosa互換）、**モノラルにダウンミックス**し **22050 Hz** に再サンプル、**ランタイム優先順**（TF→CoreML→TFLite→ONNX）、`pip install basic-pitch[tf]` でTF同梱可。([GitHub][2])
> * **PyGuitarPro**：**GP3/4/5**の読み書きに対応（AlphaTab/TuxGuitar系のPython移植）。([pyguitarpro.readthedocs.io][3])
> * **alphaTab**：ブラウザで **Guitar Pro 3–7 / MusicXML** を読み込み、譜面表示＋内蔵シンセ（alphaSynth）で再生。([alphatab.net][4], [GitHub][5])
> * **GPU in Compose**：Composeの `deploy.resources.reservations.devices` でGPU割当。ホストは **NVIDIA Container Toolkit** を導入。([Docker Documentation][6], [NVIDIA Docs][7])
> * **FastAPI/Celery/Redis**：FastAPI公式、CeleryのRedisブローカ/バックエンド利用。([FastAPI][8], [docs.celeryq.dev][9])
> * **Cursor ルール**：`.cursor/rules/*.mdc`、`description/globs/alwaysApply` 等のメタに対応。([Cursor][10])

---

# 1) リポジトリ構成（ひな形）

```
your-repo/
├─ README.md
├─ docs/
│  ├─ ARCHITECTURE.md
│  ├─ IMPLEMENTATION.md
│  └─ TESTING.md
├─ docker-compose.yml
├─ .env.example
├─ backend/
│  ├─ pyproject.toml
│  ├─ Dockerfile
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ schemas.py
│  │  ├─ deps.py
│  │  └─ celery_app.py
│  └─ workers/
│     └─ tasks.py
├─ frontend/
│  ├─ package.json
│  ├─ vite.config.ts
│  ├─ tsconfig.json
│  ├─ Dockerfile
│  └─ src/
│     ├─ main.tsx
│     ├─ App.tsx
│     └─ components/ScoreViewer.tsx
└─ .cursor/
   └─ rules/
      ├─ python-backend.mdc
      ├─ docker.mdc
      └─ frontend.mdc
```

---

# 2) ドキュメント（/docs）

## docs/ARCHITECTURE.md

````md
# アーキテクチャ

```mermaid
flowchart LR
  subgraph Client[React + alphaTab]
    U[音源アップロード] -->|POST /jobs| API
    P[譜面プレビュー(alphaTab)] -->|GET /files?type=mxl| API
    D[結果DL(MIDI/GP5/MXL)] -->|GET /files| API
  end

  subgraph Server[FastAPI]
    API[REST API] --> Q[(Redis)]
    API --> S3[(オブジェクトストレージ/ローカル)]
  end

  subgraph Workers[Celery Workers]
    W1[音源分離\nSpleeter] --> W2[AMT\nBasic Pitch]
    W2 --> W3[MIDI→TAB割当\n(自作)]
    W3 --> W4[GP5/MusicXML出力\nPyGuitarPro]
    W1 --> S3
    W2 --> S3
    W3 --> S3
    W4 --> S3
  end

  Q <--> Workers
  API <--> Workers
````

* 分離：Spleeter 4 stems（bass/drums/vocals/other）を既定。([GitHub][1])
* 採譜：Basic Pitch（多声音・楽器非依存、モノ化＆22.05kHz再サンプル）。([GitHub][2])
* TAB：自作割当ロジックで弦/フレット決定 → PyGuitarProでGP5、併せてMusicXML。
* 表示：alphaTabでGP/MusicXMLをブラウザ描画＋再生。([alphatab.net][4])
* 長時間処理：Celery+Redisで非同期実行。FastAPIはJob起票/進捗/ダウンロード。([docs.celeryq.dev][9])

````

## docs/IMPLEMENTATION.md
```md
# 実装プロセス（段階導入）

## Phase 1: 最小API & ワーカー
- POST /jobs：音源受領→ジョブ起票（202/JobID）
- GET /jobs/{id}：ステータス & 生成物URL
- タスク順：Spleeter→Basic Pitch→MIDI→TAB→保存

## Phase 2: フロント
- アップロードUI、progressポーリング、alphaTabプレビュー（MusicXML or GP5）

## Phase 3: 運用
- リトライ/キャンセル、期限付きURL、ログ/メトリクス
- GPU割当（Composeのreservations.devices + NVIDIA Container Toolkit）:contentReference[oaicite:11]{index=11}

## 設計ポリシー
- I/Oは`pathlib.Path`、型ヒント徹底、例外はエラーモデルで返却
- 音域検証：ベース音域外ノート検知→移調/破棄ポリシー
- 生成物：/midi, /gp5, /mxl, /stems をS3/ローカルに保存
````

## docs/TESTING.md

```md
# テスト

## 単体（pytest）
- Basic Pitchラッパ：出力MIDIが非空・最低限の音価を満たす
- TAB割当：弦/フレットの到達性、移動距離最小（DP）の検証
- PyGuitarPro：書出GP5を再読込して弦/フレット一致 :contentReference[oaicite:12]{index=12}

## 結合
- Spleeter→Basic Pitch：ベース音域外ノート率 < 閾値 :contentReference[oaicite:13]{index=13}
- MIDI→(GP5/MXL)→alphaTab：描画/再生できること :contentReference[oaicite:14]{index=14}

## E2E
- 代表曲アップロード→Job完了→譜面表示→DL
- パフォーマンスSLO（曲長Xに対し完了Y分以内/GPU/CPU別）

## ゴールデンデータ
- テスト用短尺オーディオと期待MIDI/TABを固定
```

---

# 3) Docker & Compose

## docker-compose.yml

```yaml
version: "3.9"
services:
  api:
    build: ./backend
    ports: ["8000:8000"]
    env_file: .env
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - FILE_BUCKET_PATH=/data
    volumes: ["./data:/data"]
    depends_on: [redis, worker]
    deploy:
      resources:
        reservations:
          devices:  # GPUを使う場合（ホストはNVIDIA Container Toolkit導入必須）
            - driver: nvidia
              count: 1
              capabilities: [gpu]  # Docker Compose GPU公式ドキュメント参照
  worker:
    build: ./backend
    command: celery -A app.celery_app.celery_app worker --loglevel=INFO
    env_file: .env
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - FILE_BUCKET_PATH=/data
    volumes: ["./data:/data"]
    depends_on: [redis]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
  redis:
    image: redis:7
    ports: ["6379:6379"]
  web:
    build: ./frontend
    ports: ["5173:5173"]
    depends_on: [api]
```

* GPU割当の書式は **Docker公式のCompose GPUサポート**と一致。ホストには **NVIDIA Container Toolkit** を導入。([Docker Documentation][6], [NVIDIA Docs][7])

## .env.example

```
FILE_BUCKET_PATH=/data
CELERY_BROKER_URL=redis://redis:6379/0
```

---

# 4) Backend（FastAPI + Celery）最小コード

## backend/pyproject.toml

```toml
[project]
name = "audio2tab"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi",
  "uvicorn[standard]",
  "celery[redis]",
  "redis",
  "spleeter",
  "basic-pitch",        # TFを同梱する場合は 'basic-pitch[tf]' :contentReference[oaicite:16]{index=16}
  "pyguitarpro",
  "soundfile",
  "librosa",
  "pydantic>=2",
]

[tool.uvicorn]
factory = false
```

## backend/Dockerfile

```dockerfile
FROM python:3.11-slim

# Spleeter動作に必要（ffmpeg/libsndfile） :contentReference[oaicite:17]{index=17}
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir uvicorn \
 && pip install --no-cache-dir .

COPY backend /app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## backend/app/schemas.py

```python
from pydantic import BaseModel
from typing import Optional, List

class JobCreated(BaseModel):
    job_id: str

class JobStatus(BaseModel):
    status: str
    files: List[str] = []
    error: Optional[str] = None
```

## backend/app/celery_app.py

```python
from celery import Celery
import os

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
celery_app = Celery("audio2tab", broker=BROKER_URL, backend=BROKER_URL)
```

## backend/app/main.py

```python
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path
import uuid, shutil, os
from .schemas import JobCreated, JobStatus
from .celery_app import celery_app
from ..workers.tasks import run_pipeline

FILE_BUCKET = Path(os.getenv("FILE_BUCKET_PATH", "/data"))
FILE_BUCKET.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="audio2tab")

@app.post("/jobs", response_model=JobCreated, status_code=202)
async def create_job(file: UploadFile = File(...), strings: int = 4):
    job_id = str(uuid.uuid4())
    job_dir = FILE_BUCKET / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    in_path = job_dir / file.filename
    with in_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    task = run_pipeline.delay(str(in_path), str(job_dir), strings)
    return JobCreated(job_id=task.id)

@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    a = celery_app.AsyncResult(job_id)
    payload = JobStatus(status=a.status)
    if a.status == "SUCCESS":
        outdir = Path(a.result.get("outdir"))
        payload.files = [str(p.name) for p in outdir.iterdir()]
    if a.status == "FAILURE":
        payload.error = str(a.result)
    return payload

@app.get("/files/{job_id}")
async def get_file(job_id: str, name: str):
    outdir = FILE_BUCKET / job_id / "out"
    path = outdir / name
    if not path.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(path)
```

## backend/workers/tasks.py

```python
from . import tasks  # noqa
```

```python
# backend/workers/tasks.py
from pathlib import Path
from celery import shared_task
from spleeter.separator import Separator
from basic_pitch.inference import predict_and_save
import guitarpro as gp

@shared_task(bind=True)
def run_pipeline(self, input_path: str, job_dir: str, strings: int = 4):
    in_path = Path(input_path)
    job = Path(job_dir)
    out = job / "out"
    out.mkdir(exist_ok=True, parents=True)

    # 1) Spleeter 4 stems（bass.wav を得る） :contentReference[oaicite:18]{index=18}
    sep = Separator('spleeter:4stems')
    sep.separate_to_file(str(in_path), str(job))

    bass_wav = next((job / in_path.stem).glob("bass.*"), None)
    if bass_wav is None:
        raise RuntimeError("bass stem not found")

    # 2) Basic Pitch でMIDI変換（CLI相当のAPI） :contentReference[oaicite:19]{index=19}
    predict_and_save(
        [str(bass_wav)],
        str(out),
        save_midi=True,
        sonify_midi=False,
        save_model_outputs=False,
        save_notes=False,
    )
    midi_path = next(out.glob("*.mid"))

    # 3) MIDI→TAB（超簡易: 全ノートを最低弦に割当 = 後で拡張）
    #    実運用では DP で移動距離最小化・和音制約など実装
    song = gp.Song()
    track = gp.Track(song, name="Bass")
    # 4弦ベース標準 E1/A1/D2/G2
    tunings = [40, 45, 50, 55][:strings]
    track.strings = [gp.String(i + 1, t) for i, t in enumerate(tunings)]
    song.tracks.append(track)
    # TODO: MIDIを読み、弦/フレット割当して song.measures に配置
    gp5 = out / f"{in_path.stem}.gp5"
    gp.write(song, str(gp5))

    return {"outdir": str(out)}
```

> **メモ**：Basic Pitchは入力を**モノにダウンミックス & 22050Hzへ再サンプル**するため、前処理は原則不要です（長尺はウィンドウ処理推奨）。([GitHub][2])

---

# 5) Frontend（React + alphaTab）最小コード

## frontend/package.json

```json
{
  "name": "audio2tab-web",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "alpha-tab": "^1.6.0",
    "zustand": "^4.5.2",
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  },
  "devDependencies": {
    "typescript": "^5.5.0",
    "vite": "^5.3.0",
    "@types/react": "^18.2.0",
    "@types/react-dom": "^18.2.0"
  }
}
```

## frontend/Dockerfile

```dockerfile
FROM node:20-slim
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend /app
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
```

## frontend/src/main.tsx

```tsx
import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'

createRoot(document.getElementById('root')!).render(<App />)
```

## frontend/src/App.tsx

```tsx
import React, { useState } from 'react'
import { ScoreViewer } from './components/ScoreViewer'

export default function App() {
  const [jobId, setJobId] = useState<string>('')
  const [file, setFile] = useState<File | null>(null)
  const [status, setStatus] = useState<any>(null)

  const submit = async () => {
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/jobs', { method: 'POST', body: form })
    const { job_id } = await res.json()
    setJobId(job_id)

    const timer = setInterval(async () => {
      const s = await fetch(`/jobs/${job_id}`).then(r => r.json())
      setStatus(s)
      if (s.status === 'SUCCESS') clearInterval(timer)
    }, 1500)
  }

  const firstScoreName = status?.files?.find((n: string) => n.endsWith('.mxl') || n.endsWith('.gp5'))

  return (
    <div style={{ padding: 16 }}>
      <h1>audio2tab</h1>
      <input type="file" accept="audio/*" onChange={e => setFile(e.target.files?.[0] ?? null)} />
      <button onClick={submit} disabled={!file}>Upload</button>
      <div>Job: {jobId} / {status?.status}</div>
      {firstScoreName && (
        <>
          <a href={`/files/${jobId}?name=${encodeURIComponent(firstScoreName)}`} download>
            Download {firstScoreName}
          </a>
          <ScoreViewer url={`/files/${jobId}?name=${encodeURIComponent(firstScoreName)}`} />
        </>
      )}
    </div>
  )
}
```

## frontend/src/components/ScoreViewer.tsx

```tsx
import React, { useEffect, useRef } from 'react'
import { alphaTab, Score } from 'alpha-tab'

export function ScoreViewer({ url }: { url: string }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const at = new alphaTab.AlphaTabApi(containerRef.current, {
      file: url,           // GP/MusicXMLを直接読み込んで描画・再生
      player: { enablePlayer: true }
    })
    return () => at.dispose()
  }, [url])

  return <div ref={containerRef} style={{ border: '1px solid #ddd', minHeight: 320 }} />
}
```

> alphaTabは **GP3–7 / MusicXML** を読み込み、内蔵シンセで再生可能です。([alphatab.net][4], [GitHub][5])

---

# 6) Cursor ルール（`.cursor/rules/*.mdc`）

> Cursor公式：**ルールは `.cursor/rules/*.mdc`**、`description/globs/alwaysApply` 等のメタを持つMDC形式。([Cursor][10])

## .cursor/rules/python-backend.mdc

```md
---
description: Backend standards for FastAPI + Celery + Spleeter + Basic Pitch
globs: ["backend/**"]
alwaysApply: false
---

# Python Backend Rules

- Python 3.11、型ヒント必須。I/Oは `pathlib.Path`。
- API: FastAPI + Pydantic v2。非同期I/Oは `async def`。
- 長時間処理は **Celery+Redis** を使用（BackgroundTasksは禁止）。:contentReference[oaicite:23]{index=23}
- **Spleeter** は 4 stems を既定とし、`ffmpeg`/`libsndfile` をDockerに入れる。CLI/ライブラリ何れも可。:contentReference[oaicite:24]{index=24}
- **Basic Pitch** は bass stem を入力に `predict_and_save` を使用。入力はmp3/m4a/wav等OK。内部でモノ化＆22050Hzリサンプル。:contentReference[oaicite:25]{index=25}
- ベースTAB割当：まずは単音貪欲→後にDP/和音対応へ拡張。
- 出力：GP5（PyGuitarPro）とMusicXMLを保存。:contentReference[oaicite:26]{index=26}
- ログ：構造化ログ（job_id付与）。
```

## .cursor/rules/docker.mdc

```md
---
description: Containerization rules with optional NVIDIA GPU
globs: ["docker-compose.yml", "**/Dockerfile"]
alwaysApply: false
---

# Docker/Compose Rules
- ベースイメージは slim。`pip install --no-cache-dir`。
- Spleeter用に `ffmpeg`/`libsndfile` を必ず導入。:contentReference[oaicite:27]{index=27}
- GPUを使う場合は Compose の `reservations.devices` を使用。ホストは **NVIDIA Container Toolkit** を導入。:contentReference[oaicite:28]{index=28}
- `api/worker/redis` の3サービスを基本構成。ボリューム `/data` を共有。
```

## .cursor/rules/frontend.mdc

```md
---
description: React + alphaTab standards for score rendering
globs: ["frontend/**"]
alwaysApply: false
---

# Frontend Rules
- React + TS + Vite。状態は軽量に（Zustand推奨）。
- スコアは **alphaTab** で表示・再生（GP or MusicXMLを直接ロード）。:contentReference[oaicite:29]{index=29}
- UX: アップロード→進捗→プレビュー/ダウンロードの3段階。
- テスト: Vitest+RTL、E2EはPlaywrightでアップロード導線を通す。
```

---

# 7) README.md（最小）

````md
# audio2tab (spleeter + basic-pitch + pyguitarpro + react + alphatab)

## Quickstart
```bash
docker compose up --build
# http://localhost:5173 を開く
````

## Pipeline

1. Spleeter 4 stems (bass stem抽出)  →  2. Basic PitchでMIDI化  →
2. 自作ロジックでベースTAB割当  →  4. PyGuitarProでGP5/MusicXML出力  →
3. alphaTabでプレビュー/再生

## 参考

* Spleeter（2/4/5 stems, ffmpeg/libsndfile 必須, CLI/Lib）([GitHub][1])
* Basic Pitch（CLI/`predict_and_save`、入力コーデック・モノ化・22.05kHz）([GitHub][2])
* PyGuitarPro（GP3–5 読み書き）([pyguitarpro.readthedocs.io][3])
* alphaTab（GP3–7/MusicXML 読込・内蔵シンセ）([alphatab.net][4])
* Compose GPU & NVIDIA Container Toolkit([Docker Documentation][6], [NVIDIA Docs][7])


[1]: https://github.com/deezer/spleeter "GitHub - deezer/spleeter: Deezer source separation library including pretrained models."
[2]: https://github.com/spotify/basic-pitch "GitHub - spotify/basic-pitch: A lightweight yet powerful audio-to-MIDI converter with pitch bend detection"
[3]: https://pyguitarpro.readthedocs.io/ "PyGuitarPro — PyGuitarPro 0.9.3 documentation"
[4]: https://alphatab.net/docs/introduction/ "Introduction | alphaTab"
[5]: https://github.com/CoderLine/alphaTab?utm_source=chatgpt.com "alphaTab is a cross platform music notation and guitar ..."
[6]: https://docs.docker.com/compose/how-tos/gpu-support/?utm_source=chatgpt.com "Enable GPU support | Docker Docs"
[7]: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html?utm_source=chatgpt.com "Installing the NVIDIA Container Toolkit"
[8]: https://fastapi.tiangolo.com/?utm_source=chatgpt.com "FastAPI"
[9]: https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/index.html?utm_source=chatgpt.com "Backends and Brokers — Celery 5.5.3 documentation"
[10]: https://docs.cursor.com/context/rules?utm_source=chatgpt.com "Cursor – Rules"
