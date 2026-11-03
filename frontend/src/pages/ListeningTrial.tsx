import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../config";
import { SegmentPlayer, SegmentState, TrackKey } from "../utils/segmentPlayer";
import { readReviewJson } from "../utils/reviewFetch";

interface ListeningTrack { key: TrackKey; url: string; sha256: string; bytes: number }
export interface ListeningSummary {
  trial_id: string; label: string; duration_seconds: number; source_job_id: string; midi_trial_id: string;
  note_count: number; tracks: ListeningTrack[];
  decay_preview?: { policy: "amplitude-half-life-v1"; half_life_seconds: 0.35 } | null;
}
const labels: Record<TrackKey, string> = {
  original: "原曲ミックス", bass: "分離ベース", synth: "採譜再生（固定音色）",
  "synth-decay": "採譜再生（減衰あり・試聴のみ）",
};
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function isListeningTrack(value: unknown, id: string): value is ListeningTrack {
  if (!isRecord(value)) return false;
  const key = value.key;
  return (key === "original" || key === "bass" || key === "synth" || key === "synth-decay")
    && value.url === `/api/v1/listening-trials/${id}/files/${key}`
    && typeof value.sha256 === "string" && /^[a-f0-9]{64}$/.test(value.sha256)
    && typeof value.bytes === "number" && Number.isInteger(value.bytes) && value.bytes > 0 && value.bytes <= 20*1024**2;
}
export function isListeningSummary(value: unknown, id: string): value is ListeningSummary {
  if (!isRecord(value) || value.trial_id !== id || typeof value.label !== "string"
    || typeof value.midi_trial_id !== "string" || !uuid.test(value.midi_trial_id)
    || typeof value.source_job_id !== "string" || !uuid.test(value.source_job_id)
    || typeof value.duration_seconds !== "number" || !Number.isFinite(value.duration_seconds) || value.duration_seconds <= 0 || value.duration_seconds > 10.01
    || typeof value.note_count !== "number" || !Number.isInteger(value.note_count) || value.note_count < 0 || value.note_count > 500
    || !Array.isArray(value.tracks) || !value.tracks.every(t => isListeningTrack(t, id))) return false;
  const keys = new Set(value.tracks.map(t => t.key));
  if (keys.size !== value.tracks.length || !keys.has("original") || !keys.has("bass") || !keys.has("synth")) return false;
  const decay = value.decay_preview;
  if (keys.has("synth-decay")) return keys.size === 4 && isRecord(decay)
    && decay.policy === "amplitude-half-life-v1" && decay.half_life_seconds === 0.35;
  return keys.size === 3 && (decay === undefined || decay === null);
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
        const body = await readReviewJson(`${API_BASE}/api/v1/listening-trials/${trialId}`, "保存された比較情報", controller.signal);
        if (!alive) return;
        if (!isListeningSummary(body, trialId)) throw new Error("保存された比較情報を読み込めません。");
        setSummary(body); setEnd(String(body.duration_seconds));
        const audioContext = new AudioContext(); context = audioContext;
        const entries = await Promise.all(body.tracks.map(async track => {
          const audio = await fetch(`${API_BASE}${track.url}`, { signal: controller.signal });
          if (!audio.ok) throw new Error(`${labels[track.key]}を読み込めません (HTTP ${audio.status})`);
          const bytes = await audio.arrayBuffer();
          if (bytes.byteLength !== track.bytes) throw new Error("音源のサイズが保存記録と異なります。");
          const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map(b => b.toString(16).padStart(2,"0")).join("");
          if (digest !== track.sha256) throw new Error("音源のハッシュが保存記録と異なります。");
          const buffer = await audioContext.decodeAudioData(bytes);
          if (Math.abs(buffer.duration-body.duration_seconds) > 1/8000) throw new Error("音源の共通時間軸が一致しません。");
          return [track.key, buffer] as const;
        }));
        if (!alive) return;
        const buffers = new Map(entries);
        const original = buffers.get("original"), bass = buffers.get("bass"), synth = buffers.get("synth");
        if (!original || !bass || !synth) throw new Error("比較に必要な音源がありません。");
        const current = new SegmentPlayer(audioContext, { original, bass, synth, "synth-decay": buffers.get("synth-decay") }, body.duration_seconds, s => { if (alive) setState(s); });
        player.current = current; setState(current.snapshot()); setReady(true);
      } catch (cause) { if (alive && !controller.signal.aborted) { player.current?.stop(); setError(cause instanceof Error ? cause.message : String(cause)); setReady(false); } }
    })();
    const timer = window.setInterval(() => { if (alive && player.current) setState(player.current.snapshot()); }, 40);
    return () => { alive = false; controller.abort(); clearInterval(timer); player.current?.dispose(); player.current = null; if (context) void context.close().catch(cause => console.error("Audio context close failed", cause)); };
  }, [trialId]);
  const act = (operation: () => void | Promise<void>): void => { setError(""); try { void Promise.resolve(operation()).catch(cause => setError(String(cause))); } catch (cause) { setError(String(cause)); } };
  const availableTracks: TrackKey[] = summary ? summary.tracks.map(track => track.key) : ["original", "bass", "synth"];
  return <article className="product-page">
    <h2>保存出力の区間比較（ローカル）</h2>
    <p>原曲 → 分離ベース → 採譜再生を同じ秒位置で切り替えます。再推論はしません。正解音符は未検証です。</p>
    <p>採譜再生は元MIDIの音高・時刻から作った固定音色です。音色・音量の一致は評価対象外で、音量正規化は行いません。</p>
    {summary?.decay_preview && <p>「減衰あり」は各音の音量だけを0.35秒ごとに半分にします。音符の開始・終了・音高、元MIDI・TAB・MusicXML・GP5・評価値は変えません。実音源の減衰を推定したものではありません。</p>}
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
    <div style={{ display:"flex", gap:8, flexWrap:"wrap", margin:"12px 0" }}>{availableTracks.map(key => <button disabled={!ready} aria-pressed={state?.track===key} key={key} onClick={() => act(() => player.current?.select(key))}>{labels[key]}</button>)}</div>
    <div className="button-row">
      <button disabled={!ready} onClick={() => act(() => player.current?.play())}>比較を再生</button>
      <button disabled={!ready} onClick={() => act(() => player.current?.pause())}>比較を一時停止</button>
      <button disabled={!ready} onClick={() => act(() => player.current?.stop())}>比較を停止（区間先頭）</button>
    </div>
    <label>比較位置 <input aria-label="比較位置" type="range" min={state?.start ?? 0} max={state?.end ?? 0} step="0.01" value={state?.position ?? 0} disabled={!ready} onChange={e => act(() => player.current?.seek(Number(e.target.value)))} /></label>
    <p>区間端とループはWeb Audioの音声クロックで制御します。画面の時刻更新は約40msごとです。区間変更は適用するまで反映されません。</p>
  </article>;
}
