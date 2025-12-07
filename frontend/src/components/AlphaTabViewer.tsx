import { useEffect, useRef } from "react";

// AlphaTab types are exported from the package; the import is lazy-used.
// eslint-disable-next-line import/no-extraneous-dependencies
import { AlphaTabApi } from "@coderline/alphatab";

export interface AlphaTabViewerProps {
  scoreUrl?: string;
}

function AlphaTabViewer({ scoreUrl }: AlphaTabViewerProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!scoreUrl || !containerRef.current) {
      return undefined;
    }

    let api: AlphaTabApi | undefined;

    try {
      api = new AlphaTabApi(containerRef.current, {
        file: scoreUrl,
      });
    } catch (err) {
      // AlphaTab may not initialize in non-browser contexts; log for debugging.
      // eslint-disable-next-line no-console
      console.warn("AlphaTab init skipped", err);
    }

    return () => {
      api?.destroy();
    };
  }, [scoreUrl]);

  return (
    <div
      ref={containerRef}
      style={{
        border: "1px dashed #d1d5db",
        padding: "12px",
        minHeight: "120px",
        borderRadius: "8px",
        background: "#f9fafb",
      }}
    >
      AlphaTab preview will render here.
    </div>
  );
}

export default AlphaTabViewer;

