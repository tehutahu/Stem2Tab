import { useEffect, useRef, useState } from "react";

// AlphaTab types are exported from the package; the import is lazy-used.
// eslint-disable-next-line import/no-extraneous-dependencies
import { AlphaTabApi, type synth } from "@coderline/alphatab";

import "./MidiTrialViewer.css";
import { installReadableEngravingLayout } from "../utils/readableEngraving";
import { ALPHATAB_RESOURCES } from "../config";
import type { FretboardNote } from "./Fretboard";

type PlayerState = "stopped" | "playing" | "paused";

export interface AlphaTabViewerProps {
  scoreUrl?: string;
  soundFontUrl?: string;
  onActiveNotesChange?: (notes: FretboardNote[]) => void;
  onPlayerStateChange?: (state: PlayerState) => void;
}

function AlphaTabViewer({
  scoreUrl,
  soundFontUrl,
  onActiveNotesChange,
  onPlayerStateChange,
}: AlphaTabViewerProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const apiRef = useRef<AlphaTabApi | null>(null);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const pendingNotesRef = useRef<FretboardNote[] | null>(null);
  const callbacks = useRef({ onActiveNotesChange, onPlayerStateChange });
  callbacks.current = { onActiveNotesChange, onPlayerStateChange };
  const [status, setStatus] = useState<"idle" | "ready" | "error">("idle");
  const [overflow, setOverflow] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);

  const destroyApi = (): void => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    pendingNotesRef.current = null;
    apiRef.current?.destroy();
    apiRef.current = null;
  };

  const mapState = (args: synth.PlayerStateChangedEventArgs): PlayerState => {
    return args.state === 1 ? "playing" : args.stopped ? "stopped" : "paused";
  };

  useEffect(() => {
    let alive = true;
    if (!scoreUrl || !containerRef.current) {
      setStatus("idle");
      setError(undefined);
      destroyApi();
      return undefined;
    }

    destroyApi();
    setOverflow(false);
    const updateOverflow = (): void => {
      const frame = frameRef.current;
      if (alive && frame) setOverflow(frame.scrollWidth > frame.clientWidth + 1);
    };
    const scoreObserver = new MutationObserver(updateOverflow);
    scoreObserver.observe(containerRef.current, { childList: true, subtree: true });

    const fail = (cause: unknown): void => {
      if (!alive) return;
      alive = false;
      callbacks.current.onPlayerStateChange?.("stopped");
      destroyApi();
      setStatus("error");
      setError(String(cause));
    };
    try {
      const api = new AlphaTabApi(containerRef.current, {
        core: { scriptFile: `${ALPHATAB_RESOURCES}alphaTab.min.js`, fontDirectory: `${ALPHATAB_RESOURCES}font/`, useWorkers: false },
        player: {
          enablePlayer: true,
          enableCursor: true,
          enableElementHighlighting: true,
          soundFont: soundFontUrl,
        },
      });

      apiRef.current = api;
      setStatus("idle");
      setError(undefined);

      api.playerReady.on(() => { if (alive) setStatus("ready"); });
      api.error.on(fail);
      api.renderFinished.on(updateOverflow);
      api.scoreLoaded.on(() => {
        if (!alive) return;
        try { installReadableEngravingLayout(api); }
        catch (cause) { fail(cause); }
      });
      api.playerStateChanged.on((args) => {
        if (!alive) return;
        const normalized = mapState(args);
        callbacks.current.onPlayerStateChange?.(normalized);
      });

      api.activeBeatsChanged.on((args) => {
        if (!alive) return;
        const beats = args?.activeBeats ?? [];
        const notes: FretboardNote[] = [];
        beats.forEach((beat: { notes: Array<{ string: number; fret: number }> }) => {
          beat.notes?.forEach((note) => {
            if (typeof note.string === "number" && typeof note.fret === "number") {
              notes.push({ string: note.string, fret: note.fret });
            }
          });
        });
        pendingNotesRef.current = notes;
        if (rafRef.current === null) {
          rafRef.current = requestAnimationFrame(() => {
            if (alive && pendingNotesRef.current) {
              callbacks.current.onActiveNotesChange?.(pendingNotesRef.current);
            }
            rafRef.current = null;
          });
        }
      });
      if (!api.load(scoreUrl)) throw new Error("Score loading could not start");
    } catch (err) {
      alive = false;
      destroyApi();
      setStatus("error");
      setError(err instanceof Error ? err.message : "AlphaTab initialization failed");
      // AlphaTab may not initialize in non-browser contexts; log for debugging.
      // eslint-disable-next-line no-console
      console.warn("AlphaTab init skipped", err);
    }

    return () => {
      alive = false;
      scoreObserver.disconnect();
      destroyApi();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scoreUrl, soundFontUrl]);

  const handlePlay = (): void => {
    apiRef.current?.play();
  };
  const handlePause = (): void => {
    apiRef.current?.pause();
  };
  const handleStop = (): void => {
    apiRef.current?.stop();
  };

  return (
    <div className="score-viewer" style={{ display: "grid", gap: "0.75rem" }}>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" onClick={handlePlay} disabled={status !== "ready"}>
          ▶ Play
        </button>
        <button type="button" onClick={handlePause} disabled={status !== "ready"}>
          ⏸ Pause
        </button>
        <button type="button" onClick={handleStop} disabled={status !== "ready"}>
          ⏹ Stop
        </button>
        <span style={{ color: "#4b5563" }}>State: {status}</span>
      </div>
      {overflow && <p>音符が密な小節は読みやすい幅を保っています。譜面内を横にスクロールしてください。</p>}
      <div ref={frameRef} className="legacy-score-frame" role="region" aria-label="譜面・横スクロール可能" tabIndex={0}>
      <div
        ref={containerRef}
        style={{
          border: "1px dashed #d1d5db",
          padding: "12px",
          minHeight: "200px",
          borderRadius: "8px",
          background: "#f9fafb",
          minWidth: 0,
        }}
      >
        AlphaTab player will render here.
      </div>
      </div>
      {error && <p style={{ color: "#dc2626" }}>AlphaTab error: {error}</p>}
    </div>
  );
}

export default AlphaTabViewer;

