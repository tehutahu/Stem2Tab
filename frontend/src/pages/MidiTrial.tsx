import { FormEvent, lazy, Suspense, useEffect, useRef, useState } from "react";
import { API_BASE } from "../config";
import { isTrialSummary, MidiTrialSummary, pitchName } from "../utils/midiTrial";
import { readBassTabConfig } from "../utils/bassTabConfig";
const Viewer = lazy(() => import("../components/MidiTrialViewer"));

export default function MidiTrial({ savedTrialId, jobSource }: { savedTrialId?: string; jobSource?: { jobId: string; name: string } } = {}): JSX.Element {
  const [sourceRead, setSourceRead] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [profile, setProfile] = useState("");
  const [source, setSource] = useState("");
  const [frets, setFrets] = useState("24");
  const [strings, setStrings] = useState("4");
  const [tuning, setTuning] = useState("28,33,38,43");
  const [gp5, setGp5] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<MidiTrialSummary | null>(null);
  const [showTab, setShowTab] = useState(() => new URLSearchParams(window.location.search).get("view") === "tab");
  const doubleClock = new URLSearchParams(window.location.search).get("notation") === "double";
  const [spacing,setSpacing]=useState<"engraved"|"time">(()=>new URLSearchParams(window.location.search).get("spacing")==="time"?"time":"engraved");
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const invalidate = (): void => { generation.current++; request.current?.abort(); request.current = null; setResult(null); setError(""); setBusy(false); };
  useEffect(() => () => { generation.current++; request.current?.abort(); }, []);
  useEffect(() => {
    if (savedTrialId === undefined) return;
    generation.current++; request.current?.abort(); setResult(null); setError(""); setBusy(false);
    if (!/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(savedTrialId)) {
      setError("保存済み試行IDが不正です。"); return;
    }
    const controller = new AbortController(); request.current = controller;
    const current = generation.current; setBusy(true);
    void (async () => {
      try {
        const response = await fetch(`${API_BASE}/api/v1/midi-trials/${savedTrialId}`, { signal: controller.signal });
        const body: unknown = await response.json();
        if (controller.signal.aborted || current !== generation.current) return;
        if (!response.ok || !isTrialSummary(body) || body.trial_id !== savedTrialId) {
          setError(`保存済み結果を読み込めません (HTTP ${response.status})`); return;
        }
        setResult(body);
        if (body.error) setError(`${body.error.code}: ${body.error.message}`);
      } catch (cause) {
        if (!controller.signal.aborted && current === generation.current) setError(`保存済み結果の読込エラー: ${cause instanceof Error ? cause.message : String(cause)}`);
      } finally {
        if (!controller.signal.aborted && current === generation.current) { setBusy(false); request.current = null; }
      }
    })();
    return () => controller.abort();
  }, [savedTrialId]);
  const jobId = jobSource?.jobId, jobName = jobSource?.name;
  useEffect(() => {
    if (jobId === undefined || jobName === undefined) return;
    invalidate(); setFile(null); setSource("local-midi");
    if (!/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(jobId) || !/^[^/\\]+\.midi?$/i.test(jobName)) {
      setError("生成MIDIのジョブIDまたはファイル名が不正です。"); return;
    }
    const abort = new AbortController(); request.current = abort;
    const gen = generation.current; setBusy(true);
    void (async () => {
      try {
        const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}`, { signal: abort.signal });
        const job = await response.json();
        if (abort.signal.aborted || gen !== generation.current) return;
        if (!response.ok || job.job_id !== jobId || job.status !== "SUCCESS" || !Array.isArray(job.files) || !job.files.includes(jobName)) throw new Error("完了したジョブのMIDIを確認できません。");
        const midi = await fetch(`${API_BASE}/api/v1/files/${jobId}?name=${encodeURIComponent(jobName)}`, { signal: abort.signal });
        if (!midi.ok) throw new Error(`生成MIDIを取得できません (HTTP ${midi.status})`);
        const bytes = await midi.blob();
        if (abort.signal.aborted || gen !== generation.current) return;
        if (bytes.size > 2 * 1024 * 1024) throw new Error("MIDIは2 MiB以下にしてください。");
        setFile(new File([bytes], jobName, { type: "audio/midi" }));
      } catch (cause) { if (!abort.signal.aborted && gen === generation.current) setError(String(cause)); }
      finally { if (gen === generation.current) { setBusy(false); request.current = null; } }
    })();
    return () => abort.abort();
  }, [jobId, jobName, sourceRead]);
  const submit = async (event: FormEvent): Promise<void> => {
    event.preventDefault(); if (request.current) return; invalidate();
    if (!file || !/\.midi?$/i.test(file.name)) { setError(".mid または .midi ファイルを選択してください。"); return; }
    if (file.size > 2 * 1024 * 1024) { setError("MIDIは2 MiB以下にしてください。"); return; }
    if (!profile || !source) { setError("入力の由来と弦構成を明示選択してください。"); return; }
    const fretCount = Number(frets), stringCount = Number(strings);
    if (!/^\d+$/.test(frets) || fretCount < 0 || fretCount > 36) { setError("最大フレットは0〜36の整数で指定してください。"); return; }
    const pitches = tuning.split(",").map((part) => Number(part.trim()));
    if (profile === "custom" && (!/^\d+$/.test(strings) || stringCount < 1 || stringCount > 7 || pitches.length !== stringCount || tuning.split(",").some((part) => !/^\d+$/.test(part.trim())) || pitches.some((p, i) => p < 0 || p > 127 || (i > 0 && p <= pitches[i - 1])))) {
      setError("カスタムは弦数1〜7、MIDI音高0〜127を低音→高音の昇順で、弦数と同じ個数指定してください。"); return;
    }
    const data = new FormData(); data.append("file", file); data.append("profile", profile); data.append("source_kind", source); data.append("max_fret", frets); data.append("gp5_preview", String(gp5));
    if (profile === "custom") { data.append("strings", strings); data.append("tuning_midi", tuning); }
    const controller = new AbortController(); request.current = controller; const current = generation.current; setBusy(true);
    try {
      const response = await fetch(`${API_BASE}/api/v1/midi-trials`, { method: "POST", body: data, signal: controller.signal });
      const body: unknown = await response.json();
      if (current !== generation.current) return;
      if (isTrialSummary(body)) { setResult(body); if (!response.ok || body.status === "failed") setError(body.error ? `${body.error.code}: ${body.error.message}` : `処理失敗 (HTTP ${response.status})`); }
      else { const detail = (body as { detail?: { code?: string; message?: string } })?.detail; setError(detail?.message ? `${detail.code ?? "error"}: ${detail.message}` : `応答形式が不正です (HTTP ${response.status})`); }
    } catch (cause) { if (current === generation.current && !controller.signal.aborted) setError(`通信・応答エラー: ${cause instanceof Error ? cause.message : String(cause)}`); }
    finally { if (current === generation.current) { setBusy(false); request.current = null; } }
  };
  const xml = result?.artifacts.find((artifact) => artifact.key === "storage-clock-xml");
  const tabConfig = result ? readBassTabConfig(result.config) : null;
  const candidateUrl = result ? `${API_BASE}/api/v1/midi-trials/${result.trial_id}/notation-clock/double.musicxml` : "";
  return <article>
    <h2>保存MIDIインポート（実験的ベース出力）</h2>
    <p>ローカルの保存MIDIを明示選択して試します。音源分離・新規推論は実行しません。入力をオクターブ移動せず保持します。上限: 2 MiB・2000音・600秒。複雑な保存クロックは処理を停止する場合があります。</p>
    {savedTrialId !== undefined && <p role="note">保存済み結果の閲覧です。再インポート・再推論は行いません。記譜テンポ候補は原版を保持して別に生成します。{busy && " 読込中…"}</p>}
    {jobSource && <p>通常採譜ジョブ {jobId} の生成MIDI: {file?.name ?? "読込待ち"}。採譜の正解は未確認です。弦設定を確認してインポートすると、元ファイルを保持したまま原時刻版から補正へ進めます。</p>}
    {jobSource && !busy && !file && <button onClick={() => setSourceRead(value => value + 1)}>生成MIDIの読込を再試行</button>}
    {busy && jobSource && !file && <button onClick={invalidate}>生成MIDIの読込を中断</button>}
    {savedTrialId === undefined && <form onSubmit={submit} style={{ display: "grid", gap: 12 }}><fieldset disabled={!!jobSource && busy && !file} style={{ display: "grid", gap: 12, border: 0, padding: 0 }}>
      <label>保存MIDI <input disabled={!!jobSource} type="file" accept=".mid,.midi" onChange={(e) => { invalidate(); setFile(e.target.files?.[0] ?? null); }} /></label>
      <label>入力の由来 <select disabled={!!jobSource} required value={source} onChange={(e) => { invalidate(); setSource(e.target.value); }}><option value="">選択してください</option><option value="muscriptor-small">Muscriptor small の保存MIDI</option><option value="local-midi">その他のローカルMIDI</option></select></label>
      <label>弦構成 <select required value={profile} onChange={(e) => { invalidate(); setProfile(e.target.value); }}><option value="">選択してください</option><option value="bass4">4弦ベース E1–G2 / MIDI 28,33,38,43</option><option value="bass5">5弦ベース B0–G2 / MIDI 23,28,33,38,43</option><option value="custom">カスタム</option></select></label>
      {profile === "custom" && <><label>弦数 <input type="number" min={1} max={7} value={strings} onChange={(e) => { invalidate(); setStrings(e.target.value); }} /></label><label>開放弦MIDI（低音→高音、カンマ区切り） <input value={tuning} onChange={(e) => { invalidate(); setTuning(e.target.value); }} /></label></>}
      <label>最大フレット <input type="number" min={0} max={36} value={frets} onChange={(e) => { invalidate(); setFrets(e.target.value); }} /></label>
      <label><input type="checkbox" checked={gp5} onChange={(e) => { invalidate(); setGp5(e.target.checked); }} /> GP5運指プレビューも生成（四分音符のみ・リズム非保持）</label>
      {gp5 && <p role="note"><strong>GP5は運指確認専用です。元MIDIの時刻・長さ・リズムを保持しません。範囲外の音が1つでもあればGP5出力を停止します。</strong></p>}
      <button disabled={busy} type="submit">{busy ? "処理中…" : "この設定でインポート"}</button>
    </fieldset></form>}
    {error && <p role="alert">{error}</p>}
    {result && <section aria-label="インポート結果">
      <h3>結果: {result.status}</h3><p>試行ID: {result.trial_id}</p>
      <p>入力: {result.original_filename} / {result.input_bytes} bytes / {result.input_note_count}音 / 保存MIDI時間 {result.source_duration_seconds}秒 / 最終音終了 {result.last_note_end_seconds}秒</p>
      <p>入力SHA-256: <code>{result.input_sha256}</code></p>
      <p>保存クロック: PPQ {result.storage_clock.ppq} / {result.storage_clock.tempo_bpm} BPM / {result.storage_clock.meter}（音楽的テンポ・拍子は未検証）</p>
      <pre aria-label="適用された設定">{JSON.stringify(result.config, null, 2)}</pre>
      <p>GP5: {result.gp5.status} / 割当数: {result.gp5.mapped_count ?? "未実行"}</p>
      {result.gp5.requested && <p><strong>GP5はリズム非保持・四分音符のみの運指プレビューです。</strong></p>}
      {result.warnings.map((warning, i) => <p key={i}>注意: {warning}</p>)}
      {!!result.gp5.unmappable.length && <><h4>GP5停止理由: 音域外の音</h4><table><thead><tr><th>元音インデックス</th><th>MIDI / 音名</th><th>開始–終了 秒</th><th>理由</th></tr></thead><tbody>{result.gp5.unmappable.map((note) => <tr key={note.source_index}><td>{note.source_index}</td><td>{note.midi} / {pitchName(note.midi)}</td><td>{note.start}–{note.end}</td><td>{note.reason}</td></tr>)}</tbody></table></>}
      <h4>成果物ダウンロード</h4><ul>{result.artifacts.filter((a) => a.key !== "gp5-preview" || result.gp5.status === "written").map((artifact) => <li key={artifact.key}><a href={`${API_BASE}${artifact.url}`} download>{artifact.key === "original-midi" ? "元MIDI（入力そのもの）" : artifact.key === "storage-clock-xml" ? "時刻保持MusicXML（保存クロック）" : artifact.key === "gp5-preview" ? "GP5運指プレビュー（リズム非保持）" : artifact.kind}: {artifact.filename}</a> ({artifact.bytes} bytes)<details><summary>SHA-256</summary><code>{artifact.sha256}</code></details></li>)}</ul>
      <p><a href={`/notation-trial/${result.trial_id}`}>この保存MIDIから運指・リズム別版を選択</a></p>
      {xml && <>
        <section aria-label="記譜テンポ変更・実時間維持">
          <h4>記譜テンポ変更・実時間維持（量子化なし）</h4>
          <p>表示中: {doubleClock ? `候補 ${result.storage_clock.tempo_bpm * 2} BPM` : `原版 ${result.storage_clock.tempo_bpm} BPM`}。音高・音の開始終了秒・再生速度は維持します。切替時は再生を停止して0秒へ戻ります。</p>
          <p>4/4と拍原点0秒（入力音源の開始）は原版から継承する表記上の仮定です。実曲の拍子・小節頭・テンポは未確認で、自動推定は行いません。</p>
          <p>{result.storage_clock.tempo_bpm}→{result.storage_clock.tempo_bpm * 2} BPMでは拍単位の音価は2倍、4/4の1小節は{240 / result.storage_clock.tempo_bpm}→{120 / result.storage_clock.tempo_bpm}秒、小節数は2倍です。末尾の休符を含む全体秒数も維持します。</p>
          <p><a href={`/midi-trial/saved/${result.trial_id}?view=${showTab ? "tab" : "staff"}${spacing === "time" ? "&spacing=time" : ""}`}>原版 {result.storage_clock.tempo_bpm} BPMで表示</a>{" / "}<a href={`/midi-trial/saved/${result.trial_id}?view=${showTab ? "tab" : "staff"}&notation=double${spacing === "time" ? "&spacing=time" : ""}`}>候補 {result.storage_clock.tempo_bpm * 2} BPMで表示（実時間維持）</a></p>
          <p><a href={candidateUrl} download="notation-clock-double.musicxml">記譜テンポ2倍の候補MusicXMLを保存（量子化なし）</a></p>
          <p role="note">この原時刻の記譜テンポ変換ではGP5出力に対応していません。上のGP5は従来の四分音符運指プレビューで、この候補版のリズム保持出力ではありません。</p>
        </section>
        <label>横配置 <select value={spacing} onChange={event=>setSpacing(event.target.value === "time" ? "time" : "engraved")}><option value="engraved">通常譜刻（読みやすさ優先）</option><option value="time">時間比例（等速カーソル・発音位置同期）</option></select></label>
        <p>横配置・TABの切替は再生を停止して0秒へ戻します。元データ・記譜テンポ・再生速度は変えません。</p>
        <label><input type="checkbox" checked={showTab} disabled={!tabConfig} onChange={(event) => setShowTab(event.target.checked)} /> 五線譜＋TAB（保存済み弦設定・原時刻保持）</label>
        {!tabConfig && <p role={showTab ? "alert" : "note"}>保存済み弦設定を確認できないため、TAB表示は利用できません。</p>}
        <Suspense fallback={<p>ビューア読込中…</p>}><Viewer key={result.trial_id} scoreUrl={doubleClock ? candidateUrl : `${API_BASE}${xml.url}`} variant={doubleClock ? "notation-clock" : "storage"} bassTab={showTab && tabConfig ? tabConfig : undefined} spacing={spacing} /></Suspense>
      </>}
    </section>}
  </article>;
}
