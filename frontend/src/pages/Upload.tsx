import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import AlphaTabViewer from "../components/AlphaTabViewer";
import { ALPHATAB_SOUNDFONT, API_BASE } from "../config";
import { JobStatus, useJobPolling } from "../hooks/useJobPolling";
import { ALPHATAB_SUPPORTED_LABEL, listAlphaTabScores, pickAlphaTabScore } from "../utils/alphaTab";

interface SubmitState {
  isSubmitting: boolean;
  error?: string;
}

function Upload(): JSX.Element {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<string | undefined>(undefined);
  const [submitState, setSubmitState] = useState<SubmitState>({ isSubmitting: false });

  const polling = useJobPolling(jobId, 2000);
  const files = polling.data?.files ?? [];

  const statusLabel = useMemo(() => {
    if (submitState.isSubmitting) return "Uploading...";
    if (polling.data?.status) return polling.data.status;
    return "Idle";
  }, [polling.data?.status, submitState.isSubmitting]);

  const progressLabel = useMemo(() => {
    if (polling.data?.progress === undefined) return undefined;
    return `${polling.data.progress}%`;
  }, [polling.data?.progress]);

  const alphaTabScores = useMemo(() => listAlphaTabScores(files), [files]);
  const [selectedScore, setSelectedScore] = useState<string | undefined>(undefined);
  useEffect(() => {
    setSelectedScore((prev) => (prev && alphaTabScores.includes(prev) ? prev : alphaTabScores[0]));
  }, [alphaTabScores]);
  const midi = useMemo(() => files.find((name) => name.toLowerCase().endsWith(".mid")), [files]);
  const scoreSelection = useMemo(() => {
    const alphaTabScore = selectedScore ?? pickAlphaTabScore(files);
    return { alphaTabScore, midi };
  }, [files, midi, selectedScore]);

  const scoreUrl = useMemo(() => {
    const scoreFile = scoreSelection.alphaTabScore;
    if (!polling.data?.job_id || !scoreFile) return undefined;
    return `${API_BASE}/api/v1/files/${polling.data.job_id}?name=${encodeURIComponent(scoreFile)}`;
  }, [polling.data?.job_id, scoreSelection.alphaTabScore]);
  const hasAlphaTabScore = Boolean(scoreSelection.alphaTabScore);
  const hasMidiOnly = !hasAlphaTabScore && Boolean(scoreSelection.midi);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setSubmitState({ isSubmitting: true, error: undefined });

    try {
      if (!selectedFile) {
        throw new Error("Please choose an audio file.");
      }

      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("strings", "4");
      formData.append("tuning", "standard");

      const response = await fetch(`${API_BASE}/api/v1/jobs`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const detail = await response.text();
        throw new Error(`Failed to create job (${response.status}) ${detail}`.trim());
      }

      const json = (await response.json()) as JobStatus;
      setJobId(json.job_id);
    } catch (err) {
      setSubmitState({
        isSubmitting: false,
        error: err instanceof Error ? err.message : "Unexpected error",
      });
      return;
    }

    setSubmitState({ isSubmitting: false, error: undefined });
  };

  return (
    <section>
      <form onSubmit={handleSubmit} style={{ display: "grid", gap: "0.75rem" }}>
        <label style={{ display: "grid", gap: "0.35rem" }}>
          <span>Upload audio</span>
          <input
            name="audio"
            type="file"
            accept="audio/*"
            onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
            disabled={submitState.isSubmitting}
          />
        </label>
        <button
          type="submit"
          disabled={submitState.isSubmitting || !selectedFile}
          style={{
            padding: "0.5rem 0.9rem",
            border: "1px solid #2563eb",
            background: "#2563eb",
            color: "#fff",
            borderRadius: "6px",
            cursor: submitState.isSubmitting || !selectedFile ? "not-allowed" : "pointer",
          }}
        >
          Create job
        </button>
      </form>

      <div style={{ marginTop: "1rem" }}>
        <p>
          Status: <strong>{statusLabel}</strong>
        </p>
        {progressLabel && (
          <p>
            Progress: <strong>{progressLabel}</strong>
          </p>
        )}
        {polling.error && <p style={{ color: "#dc2626" }}>Error: {polling.error}</p>}
        {submitState.error && <p style={{ color: "#dc2626" }}>Error: {submitState.error}</p>}
        {polling.data?.error && <p style={{ color: "#dc2626" }}>Error: {polling.data.error}</p>}
        {files.length > 0 && (
          <div style={{ marginTop: "0.75rem" }}>
            <p>Artifacts:</p>
            <ul>
              {files.map((fileName) => {
                const href = `${API_BASE}/api/v1/files/${polling.data?.job_id}?name=${encodeURIComponent(fileName)}`;
                return (
                  <li key={fileName}>
                    <a href={href} target="_blank" rel="noreferrer">
                      {fileName}
                    </a>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {!hasAlphaTabScore && files.length > 0 ? (
          <p style={{ color: "#dc2626" }}>
            AlphaTab対応のスコア({ALPHATAB_SUPPORTED_LABEL})がまだ生成されていません。
            {hasMidiOnly ? " 生成済みのMIDIはダウンロードできます (AlphaTabは未対応)。" : ""}
          </p>
        ) : null}
        {scoreUrl && (
          <div style={{ marginTop: "1rem", display: "grid", gap: "0.5rem" }}>
            {alphaTabScores.length > 1 && (
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                <label>
                  スコア切替:{" "}
                  <select
                    value={selectedScore ?? ""}
                    onChange={(event) => setSelectedScore(event.target.value || undefined)}
                  >
                    {alphaTabScores.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            )}
            <AlphaTabViewer scoreUrl={scoreUrl} soundFontUrl={ALPHATAB_SOUNDFONT} />
            {polling.data?.job_id && (
              <Link
                to={`/demo/${polling.data.job_id}`}
                style={{ color: "#2563eb", textDecoration: "underline" }}
              >
                デモを見る
              </Link>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

export default Upload;

