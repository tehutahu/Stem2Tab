import { FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { API_BASE } from "../config";

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const PENDING_KEY = "stem2tab.muscriptor.pending-request";
function storedRequest(): string | null {
  try { const id = sessionStorage.getItem(PENDING_KEY); return id && UUID.test(id) ? id : null; } catch { return null; }
}
function storeRequest(id: string | null): void {
  try { if (id) sessionStorage.setItem(PENDING_KEY, id); else sessionStorage.removeItem(PENDING_KEY); } catch { /* Recovery remains available in this page if session storage is unavailable. */ }
}
type Reason = { code: string; message: string };
type Availability = { available: boolean; reasons: Reason[]; max_audio_seconds: number; max_upload_bytes: number };
type Job = { job_id: string; status: "running" | "completed" | "failed"; stage: string; midi_trial_id: string | null; error: Reason | null };
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const reason = (v: unknown): v is Reason => record(v) && typeof v.code === "string" && typeof v.message === "string";
function availability(v: unknown): v is Availability {
  return record(v) && typeof v.available === "boolean" && Array.isArray(v.reasons) && v.reasons.every(reason) &&
    v.max_audio_seconds === 10 && v.max_upload_bytes === 10485760;
}
function jobResponse(v: unknown): v is Job {
  return record(v) && typeof v.job_id === "string" && UUID.test(v.job_id) &&
    ["running", "completed", "failed"].includes(String(v.status)) && typeof v.stage === "string" &&
    (v.midi_trial_id === null || (typeof v.midi_trial_id === "string" && UUID.test(v.midi_trial_id))) &&
    (v.error === null || reason(v.error)) && (v.status !== "completed" || v.midi_trial_id !== null);
}
function detail(v: unknown, status: number): string {
  return record(v) && reason(v.detail) ? `${v.detail.code}: ${v.detail.message}` : `応答形式が不正です (HTTP ${status})`;
}

export default function MuscriptorTrial(): JSX.Element {
  const [available, setAvailable] = useState<Availability | null>(null);
  const [availabilityError, setAvailabilityError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [method, setMethod] = useState("");
  const [rights, setRights] = useState(false);
  const [profile, setProfile] = useState("");
  const [strings, setStrings] = useState("4");
  const [tuning, setTuning] = useState("28,33,38,43");
  const [frets, setFrets] = useState("24");
  const [gp5, setGp5] = useState(false);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(storedRequest);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const submitting = useRef(false);
  const locked = busy || pendingId !== null || job?.status === "running";
  const clear = (): void => {
    generation.current++; controller.current?.abort();
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null; submitting.current = false;
  };
  useEffect(() => {
    const abort = new AbortController(); let alive = true;
    void (async () => {
      try {
        const response = await fetch(`${API_BASE}/api/v1/muscriptor-trials/availability`, { signal: abort.signal });
        const body: unknown = await response.json();
        if (!alive) return;
        if (!response.ok || !availability(body)) { setAvailabilityError(detail(body, response.status)); return; }
        setAvailable(body);
      } catch (cause) { if (alive && !abort.signal.aborted) setAvailabilityError(`配置状況を確認できません: ${String(cause)}`); }
    })();
    return () => { alive = false; abort.abort(); clear(); };
  }, []);
  useEffect(() => {
    const id = storedRequest();
    if (id) { submitting.current = true; setBusy(true); void poll(id, generation.current); }
  }, []);
  const change = (update: () => void): void => { if (locked) return; clear(); setJob(null); setError(""); update(); };
  const poll = async (id: string, current: number): Promise<void> => {
    if (current !== generation.current) return;
    const abort = new AbortController(); controller.current = abort;
    try {
      const response = await fetch(`${API_BASE}/api/v1/muscriptor-trials/${id}`, { signal: abort.signal });
      const body: unknown = await response.json();
      if (current !== generation.current || abort.signal.aborted) return;
      if (!response.ok || !jobResponse(body) || body.job_id !== id) throw new Error(detail(body, response.status));
      setJob(body);
      if (body.status === "running") timer.current = setTimeout(() => { void poll(id, current); }, 2000);
      else { submitting.current = false; setBusy(false); setPendingId(null); storeRequest(null); if (body.status === "failed") setError(body.error ? `${body.error.code}: ${body.error.message}` : "実験処理が失敗しました。"); }
    } catch (cause) { if (current === generation.current && !abort.signal.aborted) { setError(`状態確認エラー: ${String(cause)}`); setBusy(false); } }
  };
  const submit = async (event: FormEvent): Promise<void> => {
    event.preventDefault(); if (submitting.current || locked || !available?.available) return;
    setError(""); setJob(null);
    if (method !== "muscriptor-small" || !rights || !profile) { setError("方式・弦構成の選択と使用権の確認が必要です。"); return; }
    if (!file || !/\.(wav|flac)$/i.test(file.name)) { setError("WAVまたはFLACを選択してください。"); return; }
    if (file.size > available.max_upload_bytes) { setError("音源は10 MiB以下にしてください。"); return; }
    if (!/^\d+$/.test(frets) || Number(frets) > 36) { setError("最大フレットは0〜36の整数にしてください。"); return; }
    const pitches = tuning.split(",").map((part) => Number(part.trim()));
    if (profile === "custom" && (!/^[1-7]$/.test(strings) || pitches.length !== Number(strings) || tuning.split(",").some((p) => !/^\d+$/.test(p.trim())) || pitches.some((p, i) => p > 127 || (i > 0 && p <= pitches[i - 1])))) {
      setError("開放弦は1〜7弦、MIDI 0〜127を低音→高音の昇順で弦数と同じ個数指定してください。"); return;
    }
    let requestId: string;
    try { requestId = crypto.randomUUID(); } catch { setError("安全な試行IDを生成できないため、実行しませんでした。"); return; }
    if (!UUID.test(requestId)) { setError("試行IDが不正なため、実行しませんでした。"); return; }
    clear(); const current = generation.current; const abort = new AbortController(); controller.current = abort;
    setPendingId(requestId); storeRequest(requestId);
    submitting.current = true; setBusy(true);
    const data = new FormData(); data.append("request_id", requestId); data.append("file", file); data.append("method", method); data.append("rights_confirmed", "true"); data.append("profile", profile); data.append("max_fret", frets); data.append("gp5_preview", String(gp5));
    if (profile === "custom") { data.append("strings", strings); data.append("tuning_midi", tuning); }
    try {
      const response = await fetch(`${API_BASE}/api/v1/muscriptor-trials`, { method: "POST", body: data, signal: abort.signal });
      if (response.status >= 400 && response.status < 500) {
        const body: unknown = await response.json().catch(() => null);
        if (current !== generation.current || abort.signal.aborted) return;
        submitting.current = false; setBusy(false); setPendingId(null); storeRequest(null);
        setError(`実行要求は拒否されました: ${detail(body, response.status)}`); return;
      }
      const body: unknown = await response.json();
      if (current !== generation.current || abort.signal.aborted) return;
      if (!response.ok || !jobResponse(body) || body.job_id !== requestId) throw new Error(detail(body, response.status));
      setJob(body);
      if (body.status === "running") timer.current = setTimeout(() => { void poll(body.job_id, current); }, 2000);
      else { submitting.current = false; setBusy(false); setPendingId(null); storeRequest(null); if (body.status === "failed") setError(body.error ? `${body.error.code}: ${body.error.message}` : "実験処理が失敗しました。"); }
    } catch (cause) { if (current === generation.current && !abort.signal.aborted) { setBusy(false); setError(`実行結果が不明です。新しい実行は開始せず、同じ試行IDの状態を確認してください: ${String(cause)}`); } }
  };
  return <article>
    <h2>MuScriptor音源実験（明示選択・ローカル限定）</h2>
    <p>10秒まで・10 MiB以下のWAV/FLACを使う短い実験です。音源をローカルで処理します。モデル資材の自動取得は行いません。配置済み資材がない場合は実行できません。</p>
    <p>この経路は音源から新しく推論します。既存の保存MIDIを変換する経路とは別です。実行開始後の処理キャンセルには対応していません。ページを閉じてもローカル処理は継続します。</p>
    <p>音楽的な正解や精度・試聴品質を保証しません。出力MusicXMLの拍子とテンポは保存用クロックで、合成音色は元音源の音色ではありません。音高をオクターブ移動しません。</p>
    <section aria-label="配置状況"><h3>配置状況</h3>
      {availabilityError && <p role="alert">{availabilityError}</p>}
      {!available && !availabilityError && <p role="status">確認中…</p>}
      {available && <><p>{available.available ? "実験経路を利用できます" : "未配置・利用できません"}</p><ul>{available.reasons.map((r, i) => <li key={i}>{r.code}: {r.message}</li>)}</ul></>}
    </section>
    <form onSubmit={submit}>
      <fieldset disabled={!!locked}><legend>実験設定</legend>
        <p><label>実験方式 <select value={method} onChange={(e) => change(() => setMethod(e.target.value))}><option value="">選択してください</option><option value="muscriptor-small">muscriptor-small（実験）</option></select></label></p>
        <p><label>音源 WAV/FLAC <input type="file" accept=".wav,.flac" onChange={(e) => change(() => setFile(e.target.files?.[0] ?? null))} /></label></p>
        <p><label><input type="checkbox" checked={rights} onChange={(e) => change(() => setRights(e.target.checked))} /> 使用権のある音源を、非商用のローカル実験として使用することを確認しました</label></p>
        <p><label>弦構成 <select value={profile} onChange={(e) => change(() => setProfile(e.target.value))}><option value="">選択してください</option><option value="bass4">4弦ベース E1–G2 / MIDI 28,33,38,43</option><option value="bass5">5弦ベース B0–G2 / MIDI 23,28,33,38,43</option><option value="custom">カスタム</option></select></label></p>
        {profile === "custom" && <><p><label>弦数 <input type="number" min={1} max={7} value={strings} onChange={(e) => change(() => setStrings(e.target.value))} /></label></p><p><label>開放弦MIDI（低音→高音、カンマ区切り） <input value={tuning} onChange={(e) => change(() => setTuning(e.target.value))} /></label></p></>}
        <p><label>最大フレット <input type="number" min={0} max={36} value={frets} onChange={(e) => change(() => setFrets(e.target.value))} /></label></p>
        <p><label><input type="checkbox" checked={gp5} onChange={(e) => change(() => setGp5(e.target.checked))} /> GP5運指プレビュー（四分音符のみ・リズム非保持）</label></p>
        {gp5 && <p><strong>GP5は時刻・音長・リズムを保持しない運指確認専用です。範囲外の音があればGP5出力を停止します。</strong></p>}
      </fieldset>
      <button type="submit" disabled={!available?.available || !!locked}>{locked ? "実験処理中…" : "この設定でローカル実験を実行"}</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {pendingId && !job && <section aria-label="実行結果の確認"><p>結果確認待ちの試行ID: {pendingId}。応答がなくても処理が開始済みの場合があります。見つからない場合も新しい実行は開始しません。</p>
      {!busy && <button onClick={() => { setError(""); setBusy(true); void poll(pendingId, generation.current); }}>同じ試行IDの状態を確認</button>}
    </section>}
    {job && <section aria-label="実験状況"><p role="status">ジョブ {job.job_id} / {job.status} / {job.stage}</p>
      {job.status === "running" && !busy && <button onClick={() => { setError(""); setBusy(true); void poll(job.job_id, generation.current); }}>同じジョブの状態確認を再開</button>}
      {job.status === "completed" && job.midi_trial_id && <Link to={`/midi-trial/saved/${job.midi_trial_id}`}>保存MIDI・時刻保持MusicXML・運指結果を表示</Link>}
    </section>}
  </article>;
}
