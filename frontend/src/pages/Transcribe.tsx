import { FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { API_BASE } from "../config";
import { WorkflowHttpError, workflowJson } from "../utils/workflowHttp";
import OutputReview, { parseOutputReview, type OutputReviewInfo } from "../components/OutputReview";
import "./Transcribe.css";

const ROOT = `${API_BASE}/api/v1/transcriptions`;
const KEY = "stem2tab.song.request";
const EMPTY_AUDIO = "選択した音源は0バイトの空ファイルです。音声データのあるファイルを選択してください。";
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
type Health = { model_assets: "configured" | "unavailable"; reasons: { message: string }[]; max_audio_seconds: number; max_upload_bytes: number };
type Common = { job_id: string; stage: string; cancel_requested: boolean; completed_chunks: number; total_chunks: number; available_files: string[] };
type Job = Common & ({ status: "running" } | { status: "completed"; midi_trial_id: string } |
  { status: "failed" | "cancelled"; error: { message: string } | null } | { status: "needs_review"; review: OutputReviewInfo });
function parseHealth(v: unknown): Health {
  if (!record(v) || !["configured", "unavailable"].includes(String(v.model_assets)) || !Array.isArray(v.reasons) ||
    !v.reasons.every((r: unknown) => record(r) && typeof r.message === "string") || v.max_audio_seconds !== 600 || v.max_upload_bytes !== 536870912) throw new Error("配置応答が不正です。");
  return { model_assets: v.model_assets === "configured" ? "configured" : "unavailable", reasons: v.reasons, max_audio_seconds: v.max_audio_seconds, max_upload_bytes: v.max_upload_bytes };
}
function parseJob(v: unknown): Job {
  if (!record(v) || typeof v.job_id !== "string" || !UUID.test(v.job_id) || typeof v.stage !== "string" ||
    typeof v.cancel_requested !== "boolean" || typeof v.completed_chunks !== "number" || !Number.isInteger(v.completed_chunks) ||
    typeof v.total_chunks !== "number" || !Number.isInteger(v.total_chunks) || v.completed_chunks < 0 || v.total_chunks < v.completed_chunks || v.total_chunks > 120 ||
    !Array.isArray(v.available_files) || !v.available_files.every((name: unknown) => typeof name === "string" && ["original.audio", "selection.wav", "bass.wav", "performance.mid", "model-events.mid", "model-notes.json", "model-events.jsonl", "model-tokens.jsonl", "model-output-issues.json"].includes(name))) throw new Error("処理状況の応答が不正です。");
  const common = { job_id: v.job_id, stage: v.stage, cancel_requested: v.cancel_requested, completed_chunks: v.completed_chunks, total_chunks: v.total_chunks, available_files: v.available_files };
  if (v.status === "running") return { ...common, status: "running" };
  if (v.status === "needs_review") return { ...common, status: "needs_review", review: parseOutputReview(v.review) };
  if (v.status === "completed" && typeof v.midi_trial_id === "string" && UUID.test(v.midi_trial_id)) return { ...common, status: "completed", midi_trial_id: v.midi_trial_id };
  if (v.status === "failed" || v.status === "cancelled") {
    if (v.error === null) return { ...common, status: v.status, error: null };
    if (record(v.error) && typeof v.error.message === "string") return { ...common, status: v.status, error: { message: v.error.message } };
  }
  throw new Error("処理状況の応答が不正です。");
}
function stored(): string | null { try { const v = sessionStorage.getItem(KEY); return v && UUID.test(v) ? v : null; } catch { return null; } }
// A saved-job link only reads its status; it never submits or reruns audio.
function initialJob(): string | null {
  const linked = new URLSearchParams(window.location.search).get("job");
  return linked && UUID.test(linked) ? linked : stored();
}
function remember(v: string): void { try { sessionStorage.setItem(KEY, v); } catch { /* Current-page recovery still uses the same ID. */ } }
function newIdentifier(): string | null {
  try { const value = crypto.randomUUID(); return UUID.test(value) ? value : null; }
  catch { return null; }
}
const stages: Record<string, string> = { queued: "受付済み", separating: "ベースを分離中", transcribing: "MuScriptorで採譜中", exporting: "五線譜・TABを作成中", completed: "保存完了" };
const artifactLabels: Record<string, string> = { "original.audio": "元音源を保存", "selection.wav": "選択音源を保存", "bass.wav": "分離ベースを保存", "performance.mid": "原MIDIを保存",
  "model-events.mid": "全推論イベントの生MIDIを保存（ゼロ長音を含む）", "model-notes.json": "元の全音符JSONを保存", "model-events.jsonl": "発音・停止イベントを保存", "model-tokens.jsonl": "元トークンを保存", "model-output-issues.json": "音符検査記録を保存" };

export default function Transcribe(): JSX.Element {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [scope, setScope] = useState("full");
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("10");
  const [profile, setProfile] = useState("bass4");
  const [rights, setRights] = useState(false);
  const [id, setId] = useState<string | null>(initialJob);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  const sending = useRef(false);
  const cancelSending = useRef(false);
  const healthAbort = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abort = useRef<AbortController | null>(null);
  const statusPanel = useRef<HTMLElement>(null);
  const errorPanel = useRef<HTMLParagraphElement>(null);
  const locked = busy || (id !== null && (!job || job.status === "running"));
  const clear = (): void => { epoch.current++; abort.current?.abort(); if (timer.current) clearTimeout(timer.current); timer.current = null; };
  const refreshHealth = async (signal?: AbortSignal): Promise<void> => {
    healthAbort.current?.abort();
    const control = new AbortController(); healthAbort.current = control;
    if (signal?.aborted) control.abort();
    signal?.addEventListener("abort", () => control.abort(), { once: true });
    try { const value = parseHealth(await workflowJson(`${ROOT}/health`, { signal: control.signal })); control.signal.throwIfAborted(); setHealth(value); setHealthError(""); }
    catch (cause) { if (!control.signal.aborted) { setHealth(null); setHealthError(`APIに接続できません: ${String(cause)}`); } }
  };
  const accept = (value: Job, expected: string, generation: number): void => {
    if (generation !== epoch.current) return;
    if (value.job_id !== expected) throw new Error("別の処理IDの応答を受信しました。");
    setJob(value); setError("");
    if (value.status === "running") timer.current = setTimeout(() => { void poll(expected, generation); }, 750);
    else { sending.current = false; setBusy(false); }
  };
  const poll = async (expected: string, generation: number): Promise<void> => {
    if (generation !== epoch.current) return;
    const control = new AbortController(); abort.current = control;
    try { accept(parseJob(await workflowJson(`${ROOT}/${expected}`, { signal: control.signal })), expected, generation); }
    catch (cause) { if (generation === epoch.current && !control.signal.aborted) { setBusy(false); setError(`状態は未確認です: ${String(cause)}`); } }
  };
  useEffect(() => {
    const control = new AbortController(); void refreshHealth(control.signal);
    const pending = initialJob(); if (pending) { setBusy(true); void poll(pending, epoch.current); }
    return () => { control.abort(); healthAbort.current?.abort(); clear(); };
  }, []);
  // Bring a newly submitted/recovered job or an actionable error into view.
  // Polling the same job never steals focus from its recovery controls.
  useEffect(() => {
    if (error) errorPanel.current?.focus();
    else if (id) statusPanel.current?.focus();
  }, [id, error]);
  const execute = async (url: string, data: FormData, requestId: string): Promise<void> => {
    const priorJob = job; const priorId = id;
    clear(); const generation = epoch.current; const control = new AbortController(); abort.current = control;
    setId(requestId); remember(requestId); setJob(null); setError(""); setBusy(true); sending.current = true;
    try { accept(parseJob(await workflowJson(url, { method: "POST", body: data, signal: control.signal })), requestId, generation); }
    catch (cause) { if (generation === epoch.current && !control.signal.aborted) {
      setBusy(false);
      if (cause instanceof WorkflowHttpError && cause.status >= 400 && cause.status < 500) {
        sending.current = false; setId(priorId); setJob(priorJob);
        try { if (priorId) remember(priorId); else sessionStorage.removeItem(KEY); } catch { /* State remains recoverable in this page. */ }
        setError(`実行要求は拒否されました: ${cause.message}`);
      } else setError(`実行結果は未確認です。重複実行せず同じIDで確認してください: ${String(cause)}`);
    } }
  };
  const submit = (event: FormEvent): void => {
    event.preventDefault(); if (locked || sending.current || !health || health.model_assets !== "configured" || !file || !rights) return;
    if (file.size === 0) { setError(EMPTY_AUDIO); return; }
    if (file.size > health.max_upload_bytes || !/\.(wav|flac|m4a|opus|ogg)$/i.test(file.name)) { setError("512 MiB以下のWAV/FLAC/M4A/Opusを選択してください。"); return; }
    if (scope === "range" && (!Number.isFinite(Number(start)) || !Number.isFinite(Number(end)) || Number(start) < 0 || Number(end) <= Number(start) || Number(end)-Number(start) > 600)) { setError("開始と終了を指定し、長さを600秒以内にしてください。"); return; }
    const requestId = newIdentifier();
    if (!requestId) { setError("安全な処理IDを生成できないため開始しませんでした。"); return; }
    const data = new FormData(); data.append("request_id", requestId); data.append("file", file); data.append("rights_confirmed", "true"); data.append("profile", profile);
    if (scope === "range") { data.append("start_seconds", start); data.append("end_seconds", end); }
    void execute(ROOT, data, requestId);
  };
  const cancel = async (): Promise<void> => {
    if (!id || cancelSending.current || job?.status !== "running") return;
    cancelSending.current = true; clear(); const generation = epoch.current;
    const control = new AbortController(); abort.current = control; setBusy(true);
    try { accept(parseJob(await workflowJson(`${ROOT}/${id}/cancel`, { method: "POST", signal: control.signal })), id, generation); }
    catch (cause) { if (generation === epoch.current && !control.signal.aborted) { setBusy(false); setError(`取消結果は未確認です。同じIDで確認してください: ${String(cause)}`); } }
    finally { cancelSending.current = false; }
  };
  const retry = (): void => {
    if (!id || sending.current) return;
    const next = newIdentifier();
    if (!next) { setError("安全な処理IDを生成できないため開始しませんでした。"); return; }
    const data = new FormData(); data.append("request_id", next);
    void execute(`${ROOT}/${id}/retry`, data, next);
  };
  const step = !id ? 1 : job && job.status !== "running" ? 3 : 2;
  const statusTone = job?.status === "needs_review" ? "review" : error ? "attention" : job?.status ?? "pending";
  const statusTitle = job?.status === "completed" ? "採譜結果を保存しました" : job?.status === "needs_review" ? "譜面化の前に確認してください" : job?.status === "failed" ? "処理に失敗しました" : job?.status === "cancelled" ? "処理を取り消しました" : error ? "応答は未確認です" : "採譜の進行状況";
  const primaryFiles = job?.available_files.filter(name => !name.endsWith(".json") && !name.endsWith(".jsonl")) ?? [];
  const detailFiles = job?.available_files.filter(name => name.endsWith(".json") || name.endsWith(".jsonl")) ?? [];
  const download = (name: string): JSX.Element => <li key={name}><a href={`${ROOT}/${id}/files/${name}`}>{artifactLabels[name]}</a></li>;
  return <article className="product-page workflow">
    <div className="workflow-heading">
      <div><p className="eyebrow">AUDIO TO BASS TAB</p><h2>音源からベースTABを作る</h2><p className="workflow-intro">ベースラインを、弾ける形へ。<br />音源を選び、採譜した音を聴きながら五線譜・TABで確かめましょう。</p></div>
      <div className="audio-motif" aria-hidden="true">{[16, 30, 44, 24, 58, 82, 46, 66, 100, 72, 48, 88, 60, 36, 68, 44, 24, 14].map((height, index) => <span key={index} style={{ height: `${height}%` }} />)}</div>
    </div>
    <ol className="workflow-steps" aria-label="採譜の流れ">
      {["音源と範囲を選ぶ", "分離して採譜", "確認・試聴・保存"].map((label, index) => <li key={label} aria-current={step === index + 1 ? "step" : undefined} className={step > index + 1 ? "step-done" : ""}><span aria-hidden="true">0{index + 1}</span>{label}</li>)}
    </ol>
    <section className="service-card" aria-label="サービス状態">
      <div className="service-card-heading"><h3><span className={`service-dot ${health?.model_assets === "configured" ? "service-dot-ready" : ""}`} aria-hidden="true" />サービス状態</h3><button onClick={() => { void refreshHealth(); }}>接続を再確認</button></div>
      <p>Web: 接続済み / API: {health ? "接続済み" : "未確認"} / モデル: {health?.model_assets === "configured" ? "配置済み（GPUは実行時確認）" : health ? "利用できません" : "未確認"}</p>
      {healthError && <p role="alert">{healthError}。接続を再確認してください。</p>}{health?.reasons.map((r, i) => <p className="service-reason" key={i}>{r.message}</p>)}
      <p className="service-fallback">MuScriptor smallで採譜します。Basic Pitchへの自動切替はしません。</p>
      <details><summary>処理環境の詳細</summary><p>この経路にRedis/Celeryは不要です。配置済みローカル資材とGPUが必要です。</p></details>
    </section>
    <form onSubmit={submit} className="source-form">
      <fieldset disabled={locked || id !== null}><legend>1. 音源と範囲</legend>
        <div className="source-picker"><span className="source-picker-symbol" aria-hidden="true">↑</span><label>音源 WAV/FLAC/M4A/Opus <input type="file" accept=".wav,.flac,.m4a,.opus,.ogg" aria-describedby="source-limits" onChange={(e) => { const selected = e.target.files?.[0] ?? null; setFile(selected); setJob(null); setError(selected?.size === 0 ? EMPTY_AUDIO : ""); }} /></label>
          <p id="source-limits">最大512 MiB・10分まで。使用権のある音源を選んでください。</p>
          <p className="source-selection">{file ? `${file.name} / ${(file.size / 1024 / 1024).toFixed(2)} MiB` : "ファイルはまだ選択されていません"}</p>
        </div>
        <div className="source-options"><label>処理範囲 <select value={scope} onChange={(e) => setScope(e.target.value)}><option value="full">全曲（最大10分）</option><option value="range">区間を指定</option></select></label>
          <label>弦構成 <select value={profile} onChange={(e) => setProfile(e.target.value)}><option value="bass4">4弦 E1–G2</option><option value="bass5">5弦 B0–G2</option></select></label>
        </div>
        {scope === "range" && <div className="range-options"><label>開始秒 <input type="number" min="0" step="0.01" value={start} onChange={(e) => setStart(e.target.value)} /></label><span aria-hidden="true">〜</span><label>終了秒 <input type="number" min="0" step="0.01" value={end} onChange={(e) => setEnd(e.target.value)} /></label><p>処理範囲は600秒以内。選択開始を0秒とし、元音源と選択位置を保持します。</p></div>}
        <details><summary>対応形式と元データの扱い</summary><p>M4AはAAC/ALAC、.opus/.oggはOggコンテナのOpusに対応しています。処理環境のffmpegでPCMへ変換し、アップロード原本はそのまま保存します。区間の音符時刻は選択開始を0秒とし、元音源と選択位置は保持します。</p></details>
        <label className="rights-confirmation"><input type="checkbox" checked={rights} onChange={(e) => setRights(e.target.checked)} /> 使用権のある音源を非商用で処理することを確認しました</label>
      </fieldset>
      <div className="submit-row"><button type="submit" disabled={locked || id !== null || !file || file.size === 0 || !rights || health?.model_assets !== "configured"}>{locked ? "処理結果を確認中…" : "2. 分離して採譜する"}</button><p>{id ? "現在の処理を確認できます。別の音源は結果欄から選べます。" : "未送信・未保存。採譜を開始すると原音源と結果を保存します。"}</p></div>
    </form>
    {error && <p ref={errorPanel} role="alert" tabIndex={-1}>{error}</p>}
    {id && <section ref={statusPanel} tabIndex={-1} className={`job-card job-card-${statusTone}`} aria-label="処理状況">
      <p className="eyebrow">TRANSCRIPTION STATUS</p><h3>{statusTitle}</h3>
      <p className="job-status" role="status">{job?.status === "needs_review" ? "推論出力を保存済み・譜面化の確認待ち" : job ? `${stages[job.stage] ?? job.stage} / ${{ running: "処理中", completed: "完了", failed: "失敗", cancelled: "取消済み" }[job.status]}` : "応答確認待ち"}</p>
      <p className="job-id">処理ID: {id}</p>
      {error && job?.status === "running" && <p className="page-note">進捗は最後に確認できた状態です。同じ処理IDで最新の状態を確認してください。</p>}
      {job?.status === "running" && <><p>{job.total_chunks ? `採譜区間 ${job.completed_chunks} / ${job.total_chunks}` : "処理中（進捗割合は未確定）"}{job.cancel_requested ? " / 取消要求済み。停止を確認中…" : ""}</p>{job.total_chunks > 0 && <progress aria-label="採譜区間の進捗" value={job.completed_chunks} max={job.total_chunks} />}<button disabled={job.cancel_requested} onClick={() => { void cancel(); }}>処理を取り消す</button></>}
      {!busy && locked && <button onClick={() => { clear(); setBusy(true); void poll(id, epoch.current); }}>同じ処理IDを再確認</button>}
      {job && (job.status === "failed" || job.status === "cancelled") && <><p>{job.error?.message}</p><button disabled={busy || health?.model_assets !== "configured"} onClick={retry}>新しい試行として再実行（検証済み分離を再利用）</button></>}
      {job?.status === "completed" && <><p>まず試聴して音符を確認してください。採譜精度は未検証です。</p><p><Link className="action-link action-link-primary" to={`/midi-trial/saved/${job.midi_trial_id}?tempo_source_job=${id}`}>3. 試聴・五線譜・TAB・保存へ</Link></p></>}
      {job?.status === "needs_review" && <OutputReview key={id} jobId={id} info={job.review} />}
      {job && job.status !== "running" && <>{primaryFiles.length > 0 && <div className="artifact-group"><h4>保存済みファイル</h4><ul className="artifact-links">{primaryFiles.map(download)}</ul></div>}{detailFiles.length > 0 && <details><summary>音符JSON・推論記録を保存</summary><ul className="artifact-links">{detailFiles.map(download)}</ul></details>}<button onClick={() => { clear(); sending.current = false; setId(null); setJob(null); setError(""); try { sessionStorage.removeItem(KEY); } catch { /* No stored state. */ } }}>別の音源を選ぶ</button></>}
    </section>}
    <aside className="precision-note" aria-label="採譜結果について"><h3>採譜結果は、耳でも確かめてください</h3><p>長曲の実モデル動作・採譜精度は未検証です。公式の区間継続を使いますが、境界で正しい音が得られることを保証しません。不正な出力は修復せず失敗として保存します。</p><p>原音符の開始・終了・音高を保持し、運指と量子化は結果画面で別版として選べます。試聴の減衰は既定OFFです。</p>
      <details><summary>GP5出力について</summary><p>四分音符だけの旧GP5プレビューと、拍子・テンポを明示するGP5は別の出力です。</p></details>
    </aside>
  </article>;
}
