import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { API_BASE } from "../config";

const Viewer = lazy(() => import("../components/MidiTrialViewer"));
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
interface Source { trial_id: string; input_sha256: string; input_note_count: number; original_filename: string; status: string }
interface EventRow { input_ordinal: number; midi: number; start_change_ms: number; end_change_ms: number; reasons: string[] }
interface Result {
  trial_id: string; midi_trial_id: string; status: string; input_note_count?: number; source_unchanged?: boolean;
  error?: string; gp5_status?: string; gp5_reason?: string;
  request?: {config: {profile: string; max_fret: number; tuning_midi?: number[]}; fingering: boolean; max_position_shift: number|null; max_hand_span: number|null; gp5: string;
    rhythm: {tempo_bpm: number; numerator: number; denominator: number; origin_seconds: number; subdivisions_per_quarter: number; max_boundary_shift_ms: number}|null};
  fingering?: { status: string; mapped_event_count?: number; reasons?: unknown[]; movement_comparison?: unknown };
  rhythm?: { status: string; reasons?: string[]; events?: EventRow[]; changed_event_count?: number;
    max_absolute_start_change_ms?: number; max_absolute_end_change_ms?: number };
  artifacts: Array<{key: string; url: string; filename: string; sha256: string}>;
}
const names: Record<string,string> = { "original-midi":"元MIDI（変更なし）", "original-xml":"原時刻MusicXML",
  "fingering-plan":"運指の全音符記録", "rhythm-plan":"時刻変更の全音符記録", "rhythm-xml":"明示量子化MusicXML（別版）",
  "gp5-preview":"GP5・四分音符の運指プレビュー", "gp5-rhythm":"GP5・明示量子化リズム版" };
export function validResult(v: unknown, id: string, sourceId: string): v is Result {
  const r = v as Result;
  return !!r && r.trial_id === id && (r.midi_trial_id === sourceId || r.status === "running")
    && ["running","completed","blocked_optional_derivative","failed"].includes(r.status)
    && Array.isArray(r.artifacts) && r.artifacts.every(a => a && Object.prototype.hasOwnProperty.call(names,a.key)
      && a.url === `/api/v1/notation-trials/${id}/files/${a.key}` && /^[a-f0-9]{64}$/.test(a.sha256));
}

export default function NotationTrial({ midiTrialId }: { midiTrialId: string }): JSX.Element {
  const [source,setSource] = useState<Source|null>(null), [result,setResult] = useState<Result|null>(null);
  const [error,setError] = useState(""), [busy,setBusy] = useState(false), [requestId,setRequestId] = useState("");
  const [profile,setProfile] = useState(""), [tuning,setTuning] = useState(""), [frets,setFrets] = useState("24");
  const [fingering,setFingering] = useState(false), [shift,setShift] = useState("5"), [span,setSpan] = useState("4");
  const [rhythm,setRhythm] = useState(false), [bpm,setBpm] = useState(""), [meter,setMeter] = useState("");
  const [origin,setOrigin] = useState(""), [grid,setGrid] = useState(""), [limit,setLimit] = useState("");
  const [gp5,setGp5] = useState("none"), [display,setDisplay] = useState("");
  const operation = useRef<AbortController | null>(null);
  const generation = useRef(0), locked = useRef(false);
  const storageKey = `stem2tab-notation-v1:${midiTrialId}`;
  useEffect(() => {
    const gen = ++generation.current, abort = new AbortController();
    setSource(null); setResult(null); setDisplay(""); setError(""); setBusy(false); locked.current = false;
    const pending = localStorage.getItem(storageKey) ?? "";
    setRequestId(uuid.test(pending) ? pending : "");
    if (!uuid.test(midiTrialId)) { setError("保存MIDIのIDが不正です。"); return; }
    void (async () => { try {
      const response = await fetch(`${API_BASE}/api/v1/midi-trials/${midiTrialId}`, {signal:abort.signal});
      const value = await response.json() as Source;
      if (gen !== generation.current) return;
      if (!response.ok || value.trial_id!==midiTrialId || !/^[a-f0-9]{64}$/.test(value.input_sha256) || value.status==="failed") throw new Error("保存MIDIを確認できません。");
      setSource(value);
    } catch(e) { if (!abort.signal.aborted && gen===generation.current) setError(String(e)); } })();
    return () => { generation.current++; abort.abort(); operation.current?.abort(); };
  }, [midiTrialId,storageKey]);

  const receive = async (response: Response, id: string, gen: number): Promise<void> => {
    const value: unknown = await response.json();
    if (gen!==generation.current) return;
    if (!response.ok || !validResult(value,id,midiTrialId)) throw new Error(`保存結果を取得できません (HTTP ${response.status})。再送せず状態を確認してください。`);
    setResult(value); setDisplay("");
    if (value.request) {
      const r=value.request; setProfile(r.config.profile); setFrets(String(r.config.max_fret)); setTuning(r.config.tuning_midi?.join(",") ?? "");
      setFingering(r.fingering); setShift(String(r.max_position_shift ?? 5)); setSpan(String(r.max_hand_span ?? 4));setGp5(r.gp5);setRhythm(!!r.rhythm);
      if (r.rhythm) {setBpm(String(r.rhythm.tempo_bpm));setMeter(`${r.rhythm.numerator}/${r.rhythm.denominator}`);setOrigin(String(r.rhythm.origin_seconds));setGrid(String(r.rhythm.subdivisions_per_quarter));setLimit(String(r.rhythm.max_boundary_shift_ms));}
    }
  };
  const recover = async (): Promise<void> => {
    if (locked.current || !requestId) return;
    locked.current=true; setBusy(true); setError(""); const gen=generation.current;
    const controller = new AbortController(); operation.current = controller;
    try { await receive(await fetch(`${API_BASE}/api/v1/notation-trials/${requestId}`, { signal: controller.signal }),requestId,gen); }
    catch(e) { if(gen===generation.current) setError(String(e)); }
    finally { if(gen===generation.current) { locked.current=false; setBusy(false); } }
  };
  const submit = async (): Promise<void> => {
    if (locked.current || requestId || !source) return;
    setError("");
    try {
      if (!profile || !frets.trim() || !Number.isInteger(Number(frets))) throw new Error("弦設定と整数の最大フレットを指定してください。");
      const values = tuning.split(",").map(v=>Number(v.trim()));
      if (profile==="custom" && (!tuning.trim() || values.some(v=>!Number.isInteger(v)))) throw new Error("低音弦から順に実音MIDI番号を指定してください。");
      if (fingering && (!shift.trim() || !span.trim())) throw new Error("運指の移動上限と幅を明示してください。");
      if (!fingering && !rhythm) throw new Error("運指またはリズム別版を選択してください。");
      if (gp5!=="none" && !fingering) throw new Error("GP5には運指選択が必要です。");
      if (gp5==="explicit-rhythm" && !rhythm) throw new Error("リズムGP5には明示リズム別版の選択が必要です。");
      if (rhythm && [bpm,meter,origin,grid,limit].some(v=>!v.trim())) throw new Error("リズム別版はBPM・拍子・拍原点・格子・最大変更幅がすべて必要です。");
      const parts=meter.split("/").map(Number), id=crypto.randomUUID();
      const body={request_id:id,midi_trial_id:midiTrialId,input_sha256:source.input_sha256,
        config:{profile,max_fret:Number(frets),...(profile==="custom" ? {strings:values.length,tuning_midi:values}: {})},
        fingering,max_position_shift:fingering ? Number(shift):null,max_hand_span:fingering ? Number(span):null,
        rhythm:rhythm ? {tempo_bpm:Number(bpm),numerator:parts[0],denominator:parts[1],origin_seconds:Number(origin),
          subdivisions_per_quarter:Number(grid),max_boundary_shift_ms:Number(limit)}:null,gp5};
      localStorage.setItem(storageKey,id); setRequestId(id); locked.current=true; setBusy(true);
      const gen=generation.current;
      const controller = new AbortController(); operation.current = controller;
      try {
        const response=await fetch(`${API_BASE}/api/v1/notation-trials`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal:controller.signal});
        if (response.status===422 && gen===generation.current) {
          localStorage.removeItem(storageKey);setRequestId("");
          throw new Error("設定が受け付けられませんでした。処理は未実行です。数値範囲・拍子・チューニングを確認してください。");
        }
        await receive(response,id,gen);
      }
      catch(e) { if(gen===generation.current) setError(String(e)); }
      finally { if(gen===generation.current) {locked.current=false;setBusy(false);} }
    } catch(e) { setError(String(e)); }
  };
  return <article>
    <h2>保存MIDIの運指・リズム別版（実験）</h2>
    <p>推論は実行しません。元MIDIと原時刻MusicXMLを保持し、選んだ後処理だけを新しい保存先へ出力します。</p>
    <p>実行中の取り消しには未対応です。通信中断・ページ離脱後は保存試行IDの結果を確認し、自動再実行はしません。</p>
    <p><a href={`/midi-trial/saved/${midiTrialId}`}>元MIDI・原時刻版へ戻る</a></p>
    <p><a href={`/midi-trial/saved/${midiTrialId}?view=tab`}>原時刻の五線譜＋TABを表示（保存済み弦設定）</a></p>
    {source && <p>{source.original_filename} / {source.input_note_count}音 / SHA256 {source.input_sha256}</p>}
    <fieldset disabled={!source || busy || !!requestId}><legend>この別版の設定</legend>
      <label>弦設定 <select value={profile} onChange={e=>setProfile(e.target.value)}><option value="">明示選択</option><option value="bass4">4弦 E1 A1 D2 G2</option><option value="bass5">5弦 B0 E1 A1 D2 G2</option><option value="custom">カスタム（1〜7弦）</option></select></label>{" "}
      {profile==="custom" && <label>低音からのMIDIチューニング <input value={tuning} onChange={e=>setTuning(e.target.value)} placeholder="23,28,33,38,43,48" /></label>}
      <label>最大フレット <input type="number" min="0" max="36" value={frets} onChange={e=>setFrets(e.target.value)} /></label>
      <p><label><input type="checkbox" checked={fingering} onChange={e=>setFingering(e.target.checked)} />前後の移動と同時音を考慮する運指</label></p>
      {fingering && <p><label>移動上限（フレット） <input type="number" min="0" max="36" value={shift} onChange={e=>setShift(e.target.value)} /></label>{" "}<label>手の幅（フレット） <input type="number" min="0" max="36" value={span} onChange={e=>setSpan(e.target.value)} /></label></p>}
      <p>音高は変えません。音域外・同一弦の同時発音・探索上限では全体を停止し、欠落を隠しません。運指の弾きやすさは未検証です。</p>
      <p><label><input type="checkbox" checked={rhythm} onChange={e=>setRhythm(e.target.checked)} />明示した音楽クロックで量子化した別版</label></p>
      {rhythm && <div><p>実曲のテンポ・拍子は自動推定しません。値はすべて利用者が明示してください。</p>
        <label>四分音符BPM <input type="number" min="20" max="300" value={bpm} onChange={e=>setBpm(e.target.value)} /></label>{" "}
        <label>拍子 <input placeholder="4/4" value={meter} onChange={e=>setMeter(e.target.value)} /></label>{" "}
        <label>拍原点（素材秒） <input type="number" min="0" step="0.001" value={origin} onChange={e=>setOrigin(e.target.value)} /></label>{" "}
        <label>四分音符の分割数 <select value={grid} onChange={e=>setGrid(e.target.value)}><option value="">明示選択</option>{[1,2,4,8].map(n=><option key={n} value={n}>{n}</option>)}</select></label>{" "}
        <label>最大時刻変更（ms） <input type="number" min="0" max="250" value={limit} onChange={e=>setLimit(e.target.value)} /></label>
        <p>短音の消失、別々の開始の結合、重なり関係の変化を拒否します。途中のテンポ・拍子変更、連符、スイングは非対応です。別版の時刻0は指定した拍原点です。</p>
      </div>}
      <label>GP5出力 <select value={gp5} onChange={e=>setGp5(e.target.value)}><option value="none">なし</option><option value="quarter-preview">四分音符の運指プレビュー（リズム非保持）</option><option value="explicit-rhythm">明示量子化リズム版（整数BPM・固定拍子）</option></select></label>
      <p><button onClick={()=>void submit()}>選択した別版を作成</button></p>
    </fieldset>
    {busy && requestId && <button onClick={() => { generation.current++; operation.current?.abort(); locked.current = false; setBusy(false); setError("待機を中断しました。サーバー処理は継続する場合があります。同じ試行IDの結果を確認してください。"); }}>通信の待機を中断（処理取消ではありません）</button>}
    {requestId && <p>保存試行ID: {requestId} <button disabled={busy} onClick={()=>void recover()}>既存結果を確認（再実行なし）</button></p>}
    <p role="status">{busy ? "処理・確認中" : result ? `結果: ${result.status}` : requestId ? "送信結果は未確認。既存結果を確認してください。" : "設定待ち"}</p>
    {error && <p role="alert">{error}</p>}
    {result && result.status!=="running" && <section>
      <p>元データ保持: {result.source_unchanged ? "確認済み" : "未確認"} / {result.input_note_count}音 / GP5: {result.gp5_status}</p>
      {result.error && <p role="alert">{result.error}</p>}
      {result.gp5_reason && <p role="alert">{result.gp5_reason}</p>}
      <h3>運指</h3><p>{result.fingering?.status} / 割当 {result.fingering?.mapped_event_count ?? "—"}音</p>
      <pre>{JSON.stringify(result.fingering?.reasons,null,2)}</pre><pre>{JSON.stringify(result.fingering?.movement_comparison,null,2)}</pre>
      <h3>リズム別版</h3><p>{result.rhythm?.status} / 変更音 {result.rhythm?.changed_event_count ?? "—"} / 開始最大 {result.rhythm?.max_absolute_start_change_ms ?? "—"}ms / 終了最大 {result.rhythm?.max_absolute_end_change_ms ?? "—"}ms</p>
      <p>{result.rhythm?.reasons?.join(", ")}</p>
      {!!result.rhythm?.events?.length && <details><summary>音ごとの変更量・拒否理由（全音符）</summary><table><thead><tr><th>番号</th><th>MIDI音高</th><th>開始変化ms</th><th>終了変化ms</th><th>理由</th></tr></thead><tbody>{result.rhythm.events.map(e=><tr key={e.input_ordinal}><td>{e.input_ordinal}</td><td>{e.midi}</td><td>{e.start_change_ms.toFixed(3)}</td><td>{e.end_change_ms.toFixed(3)}</td><td>{e.reasons.join(", ")}</td></tr>)}</tbody></table></details>}
      <ul>{result.artifacts.map(a=><li key={a.key}><a href={`${API_BASE}${a.url}`}>{names[a.key]}</a>{(a.key.endsWith("xml") || a.key==="gp5-rhythm") && <button onClick={()=>setDisplay(a.key)}>この版を表示</button>}</li>)}</ul>
      <button disabled={busy} onClick={()=>{localStorage.removeItem(storageKey);setRequestId("");setResult(null);setDisplay("");setError("");}}>設定を編集して新しい別版へ（既存は保持）</button>
      {display && <><h3>{names[display]}</h3><Suspense fallback={<p>譜面を読み込み中</p>}><Viewer key={display+result.trial_id} scoreUrl={`${API_BASE}${result.artifacts.find(a=>a.key===display)!.url}`} variant={display==="original-xml" ? "storage" : display==="gp5-rhythm" ? "explicit-gp5" : "explicit"} /></Suspense></>}
    </section>}
  </article>;
}
