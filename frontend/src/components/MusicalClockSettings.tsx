import { useEffect, useRef, useState, type FormEvent } from "react";
import { API_BASE } from "../config";
import type { MidiTrialSummary } from "../utils/midiTrial";
import { isMusicalClockDerivative, readMusicalClock, type MusicalClockDerivative, type MusicalClockSelection } from "../utils/musicalClock";

type Version = { derivative: MusicalClockDerivative; xmlUrl: string; reportUrl: string };

/** User-declared clock settings and independent exact XML/report downloads. */
export default function MusicalClockSettings({ trial, onSelect }: {
  trial: MidiTrialSummary; onSelect: (selection: MusicalClockSelection | null) => void;
}): JSX.Element {
  const [bpm, setBpm] = useState(""), [meter, setMeter] = useState(""), [origin, setOrigin] = useState("");
  const [evidence, setEvidence] = useState(""), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [version, setVersion] = useState<Version | null>(null);
  const request = useRef<AbortController | null>(null);
  const urls = useRef<string[]>([]);
  useEffect(() => () => { request.current?.abort(); urls.current.forEach(url => URL.revokeObjectURL(url)); }, []);
  const create = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    const clock = readMusicalClock(bpm, meter, origin, evidence);
    if (!clock) { setError("BPM 20〜300（小数3桁まで）、拍子1〜12 / 2・4・8・16、拍原点0〜600秒（小数6桁まで）を明示入力してください。"); return; }
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try {
      const response = await fetch(`${API_BASE}/api/v1/musical-clock/${trial.trial_id}/derive`, {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
        body: JSON.stringify({ input_sha256: trial.input_sha256, clock }),
      });
      const body: unknown = await response.json();
      if (controller.signal.aborted) return;
      if (!response.ok || !isMusicalClockDerivative(body) || body.report.source_sha256 !== trial.input_sha256 || JSON.stringify(body.report.clock) !== JSON.stringify(clock)) {
        throw new Error(`別版を作成できません (HTTP ${response.status})。保存精度・元MIDIの検証に対応する設定を確認してください。`);
      }
      // Stop/unmount the old player before retiring its blob URL.
      onSelect(null);
      const xmlUrl = URL.createObjectURL(new Blob([body.musicxml], { type: "application/vnd.recordare.musicxml+xml" }));
      const reportUrl = URL.createObjectURL(new Blob([JSON.stringify({ report: body.report, musicxml_sha256: body.musicxml_sha256 }, null, 2)], { type: "application/json" }));
      urls.current.push(xmlUrl, reportUrl);
      setVersion({ derivative: body, xmlUrl, reportUrl });
    } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  const report = version?.derivative.report;
  return <section aria-label="音楽的クロック設定">
    <h4>音楽的BPM・拍子・拍原点（明示入力）</h4>
    <p>保存クロック {trial.storage_clock.tempo_bpm} BPM / {trial.storage_clock.meter} は秒を保持するための値です。実曲のテンポ推定値ではありません。音楽的クロックは未設定・未推定、信頼度も未評価です。</p>
    <p>MIDIの発音間隔だけでは拍・細分音符・半テンポ・倍テンポを区別できません。無拍・テンポ変動も自動判定しません。ここでは利用者が指定した一定テンポの別版を作ります。変動テンポの推定・テンポマップには未対応です。</p>
    <form onSubmit={create} style={{ display: "grid", gap: 8 }}>
      <label>音楽的BPM（四分音符） <input required type="number" min="20" max="300" step="0.001" value={bpm} onChange={event => setBpm(event.target.value)} /></label>
      <label>音楽的拍子 <input required placeholder="例: 4/4" value={meter} onChange={event => setMeter(event.target.value)} /></label>
      <label>拍原点（小節頭・元MIDI開始から秒） <input required type="number" min="0" max="600" step="0.000001" value={origin} onChange={event => setOrigin(event.target.value)} /></label>
      <label>設定の根拠メモ（任意） <input maxLength={500} value={evidence} onChange={event => setEvidence(event.target.value)} /></label>
      <p>原点より前の音も保持し、小節を前方へ延長します。音高・開始終了秒・速度・原MIDIは維持します。量子化はしません。XMLと設定レポートは別ファイルとして保存できます。</p>
      <button disabled={busy} type="submit">{busy ? "別版作成中…" : "入力した音楽的クロックで別版を作成"}</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {version && report && <div>
      <p>作成済み別版: 四分音符={report.clock.bpm} / {report.clock.numerator}/{report.clock.denominator} / 拍原点 {report.clock.origin_seconds}秒。根拠: 利用者の明示入力{report.clock.evidence ? ` / ${report.clock.evidence}` : ""}。推定・正解の検証はしていません。</p>
      <p>半テンポ {report.ambiguity_bpm[0]} / 倍テンポ {report.ambiguity_bpm[1]} BPMも解釈上の候補です。どれが拍かを判断する根拠はなく、自動適用しません。</p>
      <p><a href={version.xmlUrl} download="musical-clock.musicxml">別版MusicXMLを保存</a>{" / "}<a href={version.reportUrl} download="musical-clock-report.json">設定・根拠レポートを保存</a></p>
      {report.preview_eligible ? <button onClick={() => onSelect(report.preview_events && report.preview_horizon_ticks !== null ? { url: version.xmlUrl, proof: { bpm: report.clock.bpm, events: report.preview_events, horizonTicks: report.preview_horizon_ticks } } : null)}>この別版を通常譜刻で表示（再生停止・0秒へ戻る）</button> : <p role="note">この設定はブラウザの整数BPM・四分音符960 tickでは厳密に再現できません。近似再生は行わず、正確なMusicXMLとレポートを保存できます。</p>}
      <button onClick={() => onSelect(null)}>保存クロックの原版表示に戻る</button>
      <p>音楽的クロック別版は通常譜刻のみ対応します。時間比例表示は原版で利用できます。ブラウザ表示は合成音で、原MIDIの音色・ベロシティ・コントローラは再現しません。</p>
    </div>}
  </section>;
}
