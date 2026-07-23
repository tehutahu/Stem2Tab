import { useEffect, useRef, useState } from "react";

// AlphaTab types are exported from the package; the import is lazy-used.
// eslint-disable-next-line import/no-extraneous-dependencies
import { AlphaTabApi, type PlayerStateChangedEventArgs } from "@coderline/alphatab";

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
  const rafRef = useRef<number | null>(null);
  const pendingNotesRef = useRef<FretboardNote[] | null>(null);
  const [status, setStatus] = useState<"idle" | "ready" | "error">("idle");
  const [error, setError] = useState<string | undefined>(undefined);

  const destroyApi = (): void => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    apiRef.current?.destroy();
    apiRef.current = null;
  };

  const mapState = (args: PlayerStateChangedEventArgs): PlayerState => {
    // AlphaTab's PlayerState enum: 0=Stopped, 1=Playing, 2=Paused
    const raw = (args.state as number | string) ?? 0;
    if (raw === 1 || raw === "Playing") return "playing";
    if (raw === 2 || raw === "Paused") return "paused";
    return "stopped";
  };

  useEffect(() => {
    if (!scoreUrl || !containerRef.current) {
      setStatus("idle");
      setError(undefined);
      destroyApi();
      return undefined;
    }

    destroyApi();

    try {
      const api = new AlphaTabApi(containerRef.current, {
        file: scoreUrl,
        resources: {
          url: ALPHATAB_RESOURCES,
        },
        player: {
          enablePlayer: true,
          enableCursor: true,
          enableElementHighlighting: true,
          soundFont: soundFontUrl,
        },
      });

      apiRef.current = api;
      setStatus("ready");
      setError(undefined);

      api.playerStateChanged.on((args) => {
        const normalized = mapState(args);
        onPlayerStateChange?.(normalized);
      });

      api.activeBeatsChanged.on((args) => {
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
            if (pendingNotesRef.current) {
              onActiveNotesChange?.(pendingNotesRef.current);
            }
            rafRef.current = null;
          });
        }
      });
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "AlphaTab initialization failed");
      // AlphaTab may not initialize in non-browser contexts; log for debugging.
      // eslint-disable-next-line no-console
      console.warn("AlphaTab init skipped", err);
    }

    return () => {
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
    <div style={{ display: "grid", gap: "0.75rem" }}>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
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
      <div
        ref={containerRef}
        style={{
          border: "1px dashed #d1d5db",
          padding: "12px",
          minHeight: "200px",
          borderRadius: "8px",
          background: "#f9fafb",
          overflow: "hidden",
        }}
      >
        AlphaTab player will render here.
      </div>
      {error && <p style={{ color: "#dc2626" }}>AlphaTab error: {error}</p>}
    </div>
  );
}

export default AlphaTabViewer;

