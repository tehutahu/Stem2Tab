import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../config";
import type { MidiTrialSummary } from "../utils/midiTrial";
import { workflowJson } from "../utils/workflowHttp";
import { isTempoUuid, parseStoredTempoSource, parseTempoEstimate, tempoAdoption, TEMPO_UPLOAD_BYTES,
  type StoredTempoSource, type TempoAdoption, type TempoEstimate, type TempoExpectedSource } from "../utils/tempoEstimate";
import "./TempoEstimate.css";

type State = { kind: "idle" } | { kind: "working"; operation: "source" | "estimate" } |
  { kind: "result"; estimate: TempoEstimate } | { kind: "error"; message: string } | { kind: "cancelled" };
const ROOT = `${API_BASE}/api/v1/tempo-estimates`;
const relations = { primary: "解析した周期", alternative: "別の周期の候補", half: "半テンポの解釈", double: "倍テンポの解釈" };
function readAudio(file: File, signal: AbortSignal): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    const abort = (): void => reader.abort();
    const clean = (): void => signal.removeEventListener("abort", abort);
    reader.onload = () => { clean(); if (reader.result instanceof ArrayBuffer) resolve(reader.result); else reject(new Error("音声を読み取れません。")); };
    reader.onerror = () => { clean(); reject(new Error("音声を読み取れません。")); };
    reader.onabort = () => { clean(); reject(new DOMException("Audio read aborted", "AbortError")); };
    if (signal.aborted) { reject(new DOMException("Audio read aborted", "AbortError")); return; }
    signal.addEventListener("abort", abort, { once: true }); reader.readAsArrayBuffer(file);
  });
}

/** Explicit, read-only audio analysis. Manual clock ownership stays with its caller. */
export default function TempoEstimatePanel({ trial, sourceJobId, onAdopt, onInvalidate, adoptionDisabled = false }: {
  trial: MidiTrialSummary; sourceJobId?: string; onAdopt: (selection: TempoAdoption) => void;
  onInvalidate: () => void; adoptionDisabled?: boolean;
}): JSX.Element {
  const [mode, setMode] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [stored, setStored] = useState<StoredTempoSource | null>(null);
  const [state, setState] = useState<State>({ kind: "idle" });
  const generation = useRef(0), active = useRef(false);
  const request = useRef<AbortController | null>(null);
  const linked = sourceJobId !== undefined && isTempoUuid(sourceJobId) && isTempoUuid(trial.trial_id);
  const stop = (): void => { generation.current++; request.current?.abort(); request.current = null; active.current = false; };
  const reset = (): void => { stop(); setStored(null); setState({ kind: "idle" }); onInvalidate(); };
  useEffect(() => {
    // Changing a source hint never estimates, restores candidates, or edits the clock.
    setMode(""); setFile(null); setStored(null); setState({ kind: "idle" });
    return stop;
  }, [trial.trial_id, trial.input_sha256, sourceJobId]);
  const perform = async (operation: "source" | "estimate"): Promise<void> => {
    if (active.current) return;
    if (mode === "stored" && !linked) return;
    const selectedFile = file, selectedStored = stored;
    if (operation === "estimate" && (mode === "stored" ? !selectedStored : mode !== "upload" || !selectedFile)) return;
    stop(); const current = generation.current, controller = new AbortController(); request.current = controller; active.current = true;
    setState({ kind: "working", operation }); onInvalidate();
    const currentRequest = (): boolean => current === generation.current && !controller.signal.aborted;
    try {
      if (operation === "source") {
        if (sourceJobId === undefined) throw new Error("保存音声のリンクがありません。");
        const query = new URLSearchParams({ midi_trial_id: trial.trial_id, transcription_job_id: sourceJobId });
        const value = await workflowJson(`${ROOT}/sources?${query}`, { signal: controller.signal });
        if (!currentRequest()) return;
        setStored(parseStoredTempoSource(value, { trialId: trial.trial_id, midiSha256: trial.input_sha256, jobId: sourceJobId }));
        setState({ kind: "idle" });
      } else {
        let expected: TempoExpectedSource, value: unknown;
        if (mode === "stored" && selectedStored) {
          expected = selectedStored;
          value = await workflowJson(`${ROOT}/stored`, { method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(selectedStored) });
        } else if (mode === "upload" && selectedFile) {
          const extension = /\.(wav|flac)$/i.exec(selectedFile.name)?.[1].toLowerCase();
          if ((extension !== "wav" && extension !== "flac") || selectedFile.size < 1 || selectedFile.size > TEMPO_UPLOAD_BYTES) throw new Error("0バイトではない32 MiB以下のWAV/FLACを選んでください。");
          const bytes = await readAudio(selectedFile, controller.signal);
          const digest = await crypto.subtle.digest("SHA-256", bytes);
          if (!currentRequest()) return;
          const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
          expected = { kind: "uploaded-audio", audio_sha256: hash, audio_bytes: selectedFile.size, format: extension === "wav" ? "WAV" : "FLAC" };
          const data = new FormData(); data.append("file", selectedFile);
          value = await workflowJson(`${ROOT}/upload`, { method: "POST", signal: controller.signal, body: data });
        } else return;
        if (!currentRequest()) return;
        setState({ kind: "result", estimate: parseTempoEstimate(value, expected) });
      }
    } catch (cause) {
      if (!currentRequest()) return;
      if (mode === "stored") setStored(null);
      setState({ kind: "error", message: `BPM候補を確認できませんでした。音声と対応関係を確認し、必要な場合だけ再実行してください。${cause instanceof Error ? cause.message : String(cause)}` });
    } finally { if (currentRequest()) { active.current = false; request.current = null; } }
  };
  const busy = state.kind === "working";
  const estimate = state.kind === "result" ? state.estimate : null;
  return <section className="tempo-estimate" aria-label="音声からBPM候補を推定">
    <h4>音声からBPM候補を調べる</h4>
    <p>MIDIの保存テンポや発音間隔は使いません。音声の周期から候補を出します。推定・再読み込みで手入力、拍子、小節頭、元データ、再生は変更しません。</p>
    <p>解析は最大3区間・合計60秒です。長い音声では分散した区間を選び、実際の解析範囲を結果に表示します。短すぎる音声や弱い周期性では候補が出ない場合があります。</p>
    <label>推定する音声 <select value={mode} onChange={event => { reset(); setFile(null); setMode(event.target.value); }}>
      <option value="">音声の由来を選択してください</option>
      <option value="stored" disabled={!linked}>このMIDIに対応する保存済み選択音声</option>
      <option value="upload">WAV/FLACを明示アップロード（MIDIとの対応未検証）</option>
    </select></label>
    {sourceJobId !== undefined && !linked && <p role="note">音声リンクの処理IDを検証できません。保存音声を取り違えないため利用を停止しています。</p>}
    {!sourceJobId && <p className="page-note">保存音声へのリンクはありません。アップロード音声とMIDIが同じ素材かは自動検証できません。</p>}
    {mode === "stored" && <div>
      <p>保存した selection.wav 全体を対象にします。分離ベースは使いません。リンクの処理IDだけでは対応を信用せず、保存MIDIと音声のハッシュ・完了記録をAPIで照合します。</p>
      <button type="button" disabled={busy} onClick={() => { void perform("source"); }}>保存音声の対応を確認</button>
      {stored && <div className="tempo-source"><p role="status">対応確認済み: {stored.correspondence === "verified-review-preview" ? "明示選択したレビュー別版の音声" : "採譜完了ジョブの音声"} / {stored.duration_seconds}秒</p>
        <p>元音源での選択開始 {stored.source_origin_seconds}秒。解析位置はこの選択音声の先頭を0秒とします。小節頭や拍原点は推定しません。</p>
        <details><summary>保存音声の照合情報</summary><p>処理ID: {stored.transcription_job_id}<br />MIDI試行ID: {stored.midi_trial_id}<br />MIDI SHA-256: {stored.midi_sha256}<br />音声 SHA-256: {stored.audio_sha256}<br />{stored.audio_bytes} bytes / {stored.sample_rate} Hz / {stored.channels} ch</p></details></div>}
    </div>}
    {mode === "upload" && <div><label>推定用音声 WAV/FLAC <input type="file" accept=".wav,.flac" onChange={event => { reset(); setFile(event.target.files?.[0] ?? null); }} /></label>
      <p>32 MiB・120秒以内、8〜96 kHz・1〜2 ch。アップロードした音声と、この保存MIDIの対応は未検証です。同じ素材かを利用者が確認してください。元音源は上書きしません。</p></div>}
    {mode && <p><button type="button" disabled={busy || (mode === "stored" ? !stored : !file)} onClick={() => { void perform("estimate"); }}>選択した音声からBPM候補を推定</button></p>}
    {state.kind === "working" && <><p role="status">{state.operation === "source" ? "保存音声とMIDIの対応を確認中…" : "音声のBPM候補を推定中…"}</p><button type="button" onClick={() => { stop(); setState({ kind: "cancelled" }); onInvalidate(); }}>推定の待機を中断</button></>}
    {state.kind === "cancelled" && <p role="status">待機を中断しました。APIの解析は継続する場合があります。候補は反映していません。自動再実行はしません。</p>}
    {state.kind === "error" && <p role="alert">{state.message}</p>}
    {estimate && <div className="tempo-report">
      <p role="status">{estimate.status === "candidates" ? "BPM候補あり・未反映" : estimate.status === "unstable" ? "区間間で不安定・一定BPMは選べません" : "根拠不足・BPMは選べません"}</p>
      <p>解析手法: 音声のスペクトル変化の周期解析（spectral-flux-autocorrelation-v1）。8 kHz・10 ms刻み、解析した周期は40〜240 BPM。品質指標は未校正の周期性統計で、正解確率や実曲の精度ではありません。</p>
      <p>解析した区間（選択音声の先頭から秒）: {estimate.analyzed_intervals.map(interval => `${interval.start_seconds}〜${interval.end_seconds}`).join(" / ")}。{estimate.coverage === "full" ? "音声全体を解析しました。" : "全体の一部だけを解析しました。未解析区間のテンポ変化を見落とす場合があります。"}</p>
      <p>複数の周期グループは異なる周期の仮説です。半テンポ・倍テンポは各周期の別解釈です。拍・細分音符のどれを数えるかは未確定です。拍子・拍位相・小節頭は設定しません。</p>
      <p>候補は網羅的ではありません。強い周期性があっても拍の解釈が誤る場合があり、正しいBPMが候補内にある保証もありません。音声を聴き、必要なら手入力で判断してください。</p>
      <ul className="tempo-candidates">{estimate.candidates.map(candidate => <li key={`${candidate.family_id}:${candidate.relation}`}>
        <div><strong>{candidate.bpm} BPM</strong><span>{candidate.family_id === "pulse-1" ? "周期グループ1" : "周期グループ2"} / {relations[candidate.relation]}</span></div>
        <p>{candidate.periodicity === null ? "算術的な別解釈。品質・支持区間数は測定していません。" : `周期性指標 ${candidate.periodicity.toFixed(3)} / 支持区間 ${candidate.supporting_windows}。未校正・確率ではありません。`}</p>
        <button type="button" aria-label={`${candidate.bpm} BPMを入力候補に選ぶ（${candidate.family_id}・${relations[candidate.relation]}）`} disabled={!candidate.adoptable || adoptionDisabled} onClick={() => onAdopt(tempoAdoption(estimate, candidate))}>{candidate.bpm} BPMを入力候補に選ぶ</button>
        {!candidate.adoptable && <p>手入力の対応範囲20〜300 BPM外のため反映できません。</p>}
      </li>)}</ul>
      <ul>{estimate.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
      <details><summary>解析区間・理由・音声ハッシュを確認</summary><p>音声の対応: {estimate.source.correspondence} / {estimate.source.duration_seconds}秒 / {estimate.source.format}<br />音声 SHA-256: {estimate.source.audio_sha256}</p>
        <p>解析理由コード: {estimate.reason_codes.join(", ") || "なし"}</p><ul>{estimate.windows.map((window, index) => <li key={index}>{window.start_seconds}〜{window.end_seconds}秒 / {window.status} / BPM {window.bpm ?? "未確定"} / 周期性 {window.periodicity?.toFixed(3) ?? "未評価"} / アタック数 {window.onset_count}</li>)}</ul></details>
      <a href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(estimate, null, 2))}`} download="audio-tempo-estimate.json">音声BPM推定レポートを保存</a>
    </div>}
  </section>;
}
