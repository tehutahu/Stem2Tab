import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../config";
import { SegmentPlayer, SegmentState, TrackKey } from "../utils/segmentPlayer";

export interface ListeningSummary {
  trial_id: string; label: string; duration_seconds: number; source_job_id: string; midi_trial_id: string;
  note_count: number; tracks: Array<{ key: TrackKey; url: string; sha256: string; bytes: number }>;
}
const labels: Record<TrackKey, string> = { original: "原曲ミックス", bass: "分離ベース", synth: "採譜再生（固定音色）" };
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
export function isListeningSummary(value: unknown, id: string): value is ListeningSummary {
  const v = value as ListeningSummary;
  return !!v && v.trial_id === id && typeof v.label === "string" && uuid.test(v.midi_trial_id) && uuid.test(v.source_job_id)
    && Number.isFinite(v.duration_seconds) && v.duration_seconds > 0 && v.duration_seconds <= 10.01
    && Number.isInteger(v.note_count) && v.note_count >= 0 && v.note_count <= 500 && Array.isArray(v.tracks) && v.tracks.length === 3
    && new Set(v.tracks.map(t => t.key)).size === 3 && v.tracks.every(t => Object.prototype.hasOwnProperty.call(labels, t.key)
      && t.url === `/api/v1/listening-trials/${id}/files/${t.key}` && /^[a-f0-9]{64}$/.test(t.sha256) && t.bytes > 0 && t.bytes <= 20*1024**2);
}

export default function ListeningTrial({ trialId }: { trialId: string }): JSX.Element {
  const [summary, setSummary] = useState<ListeningSummary | null>(null);
  const [state, setState] = useState<SegmentState | null>(null);
  const [start, setStart] = useState("0"), [end, setEnd] = useState("");
  const [loop, setLoop] = useState(false), [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const player = useRef<SegmentPlayer | null>(null);
  useEffect(() => {
    const controller = new AbortController(); let alive = true; let context: AudioContext | undefined;
    setSummary(null); setState(null); setReady(false); setError(""); setStart("0"); setEnd(""); setLoop(false);
    if (!uuid.test(trialId)) { setError("比較IDが不正です。"); return; }
    void (async () => {
      try {
        const response = await fetch(`${API_BASE}/api/v1/listening-trials/${trialId}`, { signal: controller.signal });
        const body: unknown = await response.json();
        if (!alive) return;
        if (!response.ok || !isListeningSummary(body, trialId)) throw new Error("保存された比較情報を読み込めません。");
        setSummary(body); setEnd(String(body.duration_seconds));
        context = new AudioContext();
        const entries = await Promise.all(body.tracks.map(async track => {
          const audio = await fetch(`${API_BASE}${track.url}`, { signal: controller.signal });
          if (!audio.ok) throw new Error(`${labels[track.key]}を読み込めません (HTTP ${audio.status})`);
          const bytes = await audio.arrayBuffer();
          if (bytes.byteLength !== track.bytes) throw new Error("音源のサイズが保存記録と異なります。");
          const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map(b => b.toString(16).padStart(2,"0")).join("");
          if (digest !== track.sha256) throw new Error("音源のハッシュが保存記録と異なります。");
          const buffer = await context!.decodeAudioData(bytes);
          if (Math.abs(buffer.duration-body.duration_seconds) > 1/8000) throw new Error("音源の共通時間軸が一致しません。");
          return [track.key, buffer] as const;
        }));
        if (!alive) return;
        const current = new SegmentPlayer(context, Object.fromEntries(entries) as Record<TrackKey,AudioBuffer>, body.duration_seconds, s => { if (alive) setState(s); });
        player.current = current; setState(current.snapshot()); setReady(true);
      } catch (cause) { if (alive && !controller.signal.aborted) { player.current?.stop(); setError(cause instanceof Error ? cause.message : String(cause)); setReady(false); } }
    })();
    const timer = window.setInterval(() => { if (alive && player.current) setState(player.current.snapshot()); }, 40);
    return () => { alive = false; controller.abort(); clearInterval(timer); player.current?.dispose(); player.current = null; if (context) void context.close().catch(cause => console.error("Audio context close failed", cause)); };
  }, [trialId]);
  const act = (operation: () => void | Promise<void>): void => { setError(""); try { void Promise.resolve(operation()).catch(cause => setError(String(cause))); } catch (cause) { setError(String(cause)); } };
  return <article>
    <h2>保存出力の区間比較（ローカル）</h2>
    <p>原曲 → 分離ベース → 採譜再生を同じ秒位置で切り替えます。再推論はしません。正解音符は未検証です。</p>
    <p>採譜再生は元MIDIの音高・時刻から作った固定音色です。音色・音量の一致は評価対象外で、音量正規化は行いません。</p>
    {summary && <><h3>{summary.label}</h3><p>共通区間 0〜{summary.duration_seconds}秒 / 保存音符 {summary.note_count}音</p>
      <a href={`/midi-trial/saved/${summary.midi_trial_id}`}>元MIDI・原時刻MusicXMLを開く</a>{" / "}<a href={`/notation-trial/${summary.midi_trial_id}`}>運指・リズム別版を選択</a></>}
    <p role="status">{ready ? "読込完了" : "読込待ち"} / {state?.playing ? "再生中" : "停止または一時停止"} / {state ? labels[state.track] : "未選択"}</p>
    {error && <p role="alert">{error}</p>}
    <fieldset disabled={!ready}><legend>比較区間（素材先頭からの秒）</legend>
      <label>開始秒 <input type="number" min="0" step="0.01" value={start} onChange={e => { player.current?.pause(); setStart(e.target.value); }} /></label>{" "}
      <label>終了秒 <input type="number" min="0" step="0.01" value={end} onChange={e => { player.current?.pause(); setEnd(e.target.value); }} /></label>{" "}
      <label><input type="checkbox" checked={loop} onChange={e => { player.current?.pause(); setLoop(e.target.checked); }} />区間ループ</label>{" "}
      <button onClick={() => act(() => { if (!start.trim() || !end.trim()) throw new Error("開始・終了を指定してください。"); player.current?.configure(Number(start), Number(end), loop); })}>区間を適用</button>
    </fieldset>
    {state && <p>適用中: {state.start.toFixed(2)}〜{state.end.toFixed(2)}秒 / ループ{state.loop ? "あり" : "なし"} / 現在 {state.position.toFixed(2)}秒</p>}
    <div style={{ display:"flex", gap:8, flexWrap:"wrap", margin:"12px 0" }}>{(Object.keys(labels) as TrackKey[]).map(key => <button disabled={!ready} aria-pressed={state?.track===key} key={key} onClick={() => act(() => player.current?.select(key))}>{labels[key]}</button>)}</div>
    <div style={{display:"flex",gap:8}}>
      <button disabled={!ready} onClick={() => act(() => player.current?.play())}>比較を再生</button>
      <button disabled={!ready} onClick={() => act(() => player.current?.pause())}>比較を一時停止</button>
      <button disabled={!ready} onClick={() => act(() => player.current?.stop())}>比較を停止（区間先頭）</button>
    </div>
    <label>比較位置 <input aria-label="比較位置" type="range" min={state?.start ?? 0} max={state?.end ?? 0} step="0.01" value={state?.position ?? 0} disabled={!ready} onChange={e => act(() => player.current?.seek(Number(e.target.value)))} /></label>
    <p>区間端とループはWeb Audioの音声クロックで制御します。画面の時刻更新は約40msごとです。区間変更は適用するまで反映されません。</p>
  </article>;
}
