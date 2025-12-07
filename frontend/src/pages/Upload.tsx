import { FormEvent, useMemo, useState } from "react";

import AlphaTabViewer from "../components/AlphaTabViewer";
import { JobStatus, useJobPolling } from "../hooks/useJobPolling";

interface SubmitState {
  isSubmitting: boolean;
  error?: string;
}

function Upload(): JSX.Element {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<string | undefined>(undefined);
  const [submitState, setSubmitState] = useState<SubmitState>({ isSubmitting: false });

  const polling = useJobPolling(jobId, 2000);

  const statusLabel = useMemo(() => {
    if (submitState.isSubmitting) return "Uploading...";
    if (polling.data?.status) return polling.data.status;
    return "Idle";
  }, [polling.data?.status, submitState.isSubmitting]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setSubmitState({ isSubmitting: true, error: undefined });

    try {
      if (!selectedFile) {
        throw new Error("Please choose an audio file.");
      }

      const response = await fetch("/api/v1/jobs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ filename: selectedFile.name }),
      });

      if (!response.ok) {
        throw new Error(`Failed to create job (${response.status})`);
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
        {polling.error && <p style={{ color: "#dc2626" }}>Error: {polling.error}</p>}
        {submitState.error && <p style={{ color: "#dc2626" }}>Error: {submitState.error}</p>}
        {polling.data?.result && (
          <div style={{ marginTop: "1rem" }}>
            <AlphaTabViewer scoreUrl={undefined} />
          </div>
        )}
      </div>
    </section>
  );
}

export default Upload;

