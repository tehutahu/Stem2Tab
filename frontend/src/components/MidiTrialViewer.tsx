import { useEffect, useRef, useState } from "react";
import { AlphaTabApi, model } from "@coderline/alphatab";
import { pitchName } from "../utils/midiTrial";
import { buildCursorBars, cursorAtTick, type CursorBar } from "../utils/scoreCursor";
import { applyBassTab } from "../utils/scoreTab";
import type { BassTabConfig } from "../utils/bassTabConfig";
import { buildTimeScore, type TimeScore } from "../utils/timeScore";
import { installLibraryTimeLayout, buildLibraryTimeCursor, LIBRARY_TIME_RATE } from "../utils/libraryTimeLayout";
import { PlaybackTransport } from "../utils/playbackTransport";
import "./MidiTrialViewer.css";

export default function MidiTrialViewer({ scoreUrl, variant = "storage", bassTab, spacing = "engraved" }: { scoreUrl: string; variant?: "storage" | "notation-clock" | "explicit" | "explicit-gp5"; bassTab?: BassTabConfig; spacing?: "engraved" | "time" }): JSX.Element {
  const container = useRef<HTMLDivElement>(null);
  const cursor = useRef<HTMLDivElement>(null);
  const apiRef = useRef<AlphaTabApi | null>(null);
  const failed = useRef(false);
  const transportRef = useRef<PlaybackTransport | null>(null);
  const [rendered, setRendered] = useState(false);
  const [ready, setReady] = useState(false);
  const [state, setState] = useState("停止");
  const [pending, setPending] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [inspection, setInspection] = useState("読込中");
  const [error, setError] = useState("");
  const [cursorError, setCursorError] = useState("");
  const [timeLayout, setTimeLayout] = useState<TimeScore | null>(null);
  const tuningKey = bassTab?.tuning.join(",");
  const maxFret = bassTab?.maxFret;
  useEffect(() => {
    let alive = true;
    let paths: CursorBar[] = [], currentTick = 0, previousBar = -1, previousY = -1;
    let timed: TimeScore | null = null;
    const updateCursor = (): void => {
      const element = cursor.current;
      if (!alive || !element) return;
      const point = cursorAtTick(paths, currentTick);
      element.hidden = !point;
      if (!point) return;
      element.style.transform = `translate(${point.x}px, ${point.y}px)`;
      element.style.height = `${point.height}px`;
      element.dataset.bar = String(point.bar + 1); element.dataset.tick = String(point.tick);
      if ((previousBar !== point.bar || previousY !== point.y) && currentTick > 0) {
        const rect = element.getBoundingClientRect();
        if (rect.bottom > window.innerHeight || rect.top < 0) element.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
      previousBar = point.bar;
      previousY = point.y;
    };
    const rebuildCursor = (): void => {
      if (!alive || !api) return;
      try { paths = spacing === "time" && timed ? buildLibraryTimeCursor(api,timed.tempo) : buildCursorBars(api); setCursorError(""); }
      catch (cause) { paths = []; setCursorError(String(cause)); }
      updateCursor();
    };
    setCursorError("");
    setTimeLayout(null);
    if (cursor.current) cursor.current.hidden = true;
    failed.current = false;
    const fail = (cause: unknown): void => {
      if (!alive) return;
      const wasFailed = failed.current;
      failed.current = true; setReady(false);
      transport?.dispose(); setPending(false);
      let message = cause instanceof Error ? cause.message : String(cause);
      if (!wasFailed) { try { api?.stop(); } catch (stopError) { message += ` / stop: ${String(stopError)}`; } }
      setError(message);
    };
    setRendered(false); setReady(false); setState("停止"); setPending(false); setPosition(0); setDuration(0); setError(""); setInspection("読込中");
    if (!container.current) return;
    let api: AlphaTabApi | undefined;
    let transport: PlaybackTransport | undefined;
    try {
      api = new AlphaTabApi(container.current, {
        core: { scriptFile: "/alphatab/alphaTab.min.js", fontDirectory: "/alphatab/font/", engine: "svg", useWorkers: false, enableLazyLoading: false },
        display: spacing === "time" ? { barsPerRow: 1 } : {},
        // SDK animated cursors extrapolate past the next beat; use absolute ticks.
        player: { enablePlayer: true, soundFont: "/alphatab/soundfont/sonivox.sf2", enableCursor: false },
      });
      apiRef.current = api;
      transport = new PlaybackTransport({
        play: () => api!.play(), pause: () => api!.pause(), stop: () => api!.stop(),
        seek: time => { api!.timePosition = time; },
      }, snapshot => {
        if (!alive || failed.current) return;
        setState(snapshot.mode === "playing" ? "再生中" : snapshot.mode === "paused" ? "一時停止" : "停止");
        setPending(snapshot.pending); setPosition(snapshot.time / 1000); setDuration(snapshot.end / 1000);
        currentTick = snapshot.tick; updateCursor();
      }, fail);
      transportRef.current = transport;
      api.scoreLoaded.on((score) => {
        if (!alive) return;
        if (tuningKey !== undefined && maxFret !== undefined) {
          try { applyBassTab(score, { tuning: tuningKey.split(",").map(Number), maxFret }); }
          catch (cause) { fail(cause); return; }
        }
        // GP5 has no serialized clef in this writer. Use a bass clef for this
        // explicitly selected bass derivative only; sounding notes stay intact.
        if (variant === "explicit-gp5") {
          for (const track of score.tracks) for (const staff of track.staves) {
            staff.displayTranspositionPitch = 0;
            for (const bar of staff.bars) bar.clef = model.Clef.F4;
          }
        }
        const pitches: number[] = [];
        const programs: number[] = [];
        for (const track of score.tracks) {
          programs.push(track.playbackInfo.program);
          for (const staff of track.staves) for (const bar of staff.bars) for (const voice of bar.voices)
            for (const beat of voice.beats) for (const note of beat.notes)
              if (!note.isTieDestination) pitches.push(note.realValue);
        }
        const low = Math.min(...pitches), high = Math.max(...pitches);
        setInspection(`論理発音数 ${pitches.length} / 音域 ${pitches.length ? `${low} (${pitchName(low)})–${high} (${pitchName(high)})` : "なし"} / 合成音プログラム (0始まり) ${programs.join(", ")}`);
        if (spacing === "time") {
          try { timed=buildTimeScore(score,tuningKey !== undefined); installLibraryTimeLayout(api!); setTimeLayout(timed); }
          catch(cause) { fail(cause); return; }
        }
      });
      api.renderFinished.on(() => { if (alive) {
        setRendered(true); rebuildCursor();
      } });
      api.playerReady.on(() => { if (alive && !failed.current) { setReady(true); rebuildCursor(); } });
      api.playerStateChanged.on(args => { if (alive && !failed.current) transport?.stateChanged(args); });
      api.playerPositionChanged.on(args => { if (alive && !failed.current) transport?.positionChanged(args); });
      api.error.on(fail);
      if (!api.load(scoreUrl)) fail("MusicXMLの読込を開始できませんでした。");
    } catch (cause) { fail(cause); }
    return () => { alive = false; transport?.dispose(); if (transportRef.current === transport) transportRef.current = null; if (apiRef.current === api) apiRef.current = null; api?.destroy(); };
  }, [scoreUrl, variant, tuningKey, maxFret, spacing]);
  const preservesTime = variant === "storage" || variant === "notation-clock";
  return <section aria-label={preservesTime ? "時刻保持MusicXMLプレビュー" : "明示リズム別版プレビュー"}>
    {variant === "notation-clock" && <p><strong>記譜テンポ2倍の候補版・実時間維持。音高、開始・終了秒、再生速度は原版と同じです。量子化ではなく、小節を区切り直した比較用の表記です。実曲のテンポが確定した意味ではありません。</strong></p>}
    <p>{preservesTime ? "時刻保持MusicXMLの合成音プレビューです。元音声・元MIDIの再生ではありません。表示上の4/4とテンポは表記のためのクロックで、音楽的な拍子・テンポは未検証です。移調は行いません。" : "利用者が明示したBPM・拍子・拍原点による量子化別版です。原時刻版ではありません。合成音色で再生し、時刻0は指定した拍原点です。音楽的正解・原音色の一致を保証しません。"}</p>
    {variant === "explicit-gp5" && <p>GP5は実音高をヘ音記号で表示します（慣習的な1オクターブ上の記譜は使いません）。実音高・弦・フレットは変えません。他のソフトでの音部記号・表示オクターブは未検証です。</p>}
    <p role="status">描画: {rendered ? "完了" : "待機"} / 再生準備: {ready ? "完了" : "待機"} / 状態: {state}{pending ? "（操作反映中）" : ""}</p>
    <p>{inspection}</p>
    {spacing === "time" ? <p>時間比例の通常譜刻: 元の譜刻エンジン・音楽記号を使い、1秒{LIBRARY_TIME_RATE}pxで発音位置と赤線を合わせます。1小節ごとに改行します。音高・開始終了秒は保持し、量子化しません。元MusicXMLの音価記号を保持するため、未量子化の端数秒を記号だけで厳密に読める譜面ではありません。TABの括弧数字はタイ継続です。</p> : <p>赤線は再生時刻から音符・休符の間を進みます。通常譜刻の横間隔は時間に比例しないため、一定テンポでも画面上の速度は変わります。段替わりでは次段の左へ移ります。</p>}
    {bassTab && <p>五線譜＋TAB: 保存済み開放弦（低→高）{bassTab.tuning.map(pitchName).join(" / ")}、{bassTab.maxFret}フレット。高音側の弦を優先する簡易運指です。音高・原時刻を維持し、運指最適化やリズム補正は行いません。</p>}
    <div className="midi-trial-controls">
    <div style={{ display: "flex", gap: 8 }}>
      <button disabled={!ready} onClick={() => {
        const api = apiRef.current;
        if (failed.current || !api?.isReadyForPlayback) return;
        transportRef.current?.request("play");
      }}>再生</button>
      <button disabled={!ready} onClick={() => transportRef.current?.request("pause")}>一時停止</button>
      <button disabled={!ready} onClick={() => transportRef.current?.request("stop")}>停止</button>
    </div>
    <label>再生位置（秒） <input type="range" min={0} max={duration || 0} step={0.01} value={Math.min(position, duration)} disabled={!ready || !duration} onChange={event => transportRef.current?.request("seek", Number(event.target.value)*1000)} /></label>
    <p>{position.toFixed(2)} / {duration.toFixed(2)} 秒</p>
    </div>
    {error && <p role="alert">描画・再生エラー: {error}</p>}
    {cursorError && <p role="alert">カーソル: {cursorError}</p>}
    <div className="midi-trial-score-frame">
      <div className="midi-trial-score" ref={container} style={{ minHeight: 180, minWidth: spacing === "time" && timeLayout ? 240 + 240/timeLayout.tempo*LIBRARY_TIME_RATE : undefined }} />
      <div ref={cursor} hidden className="midi-trial-time-cursor" data-testid="score-time-cursor" aria-hidden="true" />
    </div>
  </section>;
}
