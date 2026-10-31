import { FormEvent, lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ALPHATAB_SOUNDFONT, API_BASE } from "../config";
import { JobStatus, useJobPolling } from "../hooks/useJobPolling";
import { ALPHATAB_SUPPORTED_LABEL, listAlphaTabScores, pickAlphaTabScore } from "../utils/alphaTab";

interface SubmitState {
  isSubmitting: boolean;
  error?: string;
}

// AlphaTab includes its renderer and player, so defer downloading it until a
// score is available to display.
const AlphaTabViewer = lazy(() => import("../components/AlphaTabViewer"));

function Upload(): JSX.Element {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [lookup, setLookup] = useState("");
  const [jobId, setJobId] = useState<string | undefined>(undefined);
  const [submitState, setSubmitState] = useState<SubmitState>({ isSubmitting: false });

  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  useEffect(() => () => { generation.current++; request.current?.abort(); }, []);
  const polling = useJobPolling(jobId, 2000);
  useEffect(() => { if (polling.data && ["SUCCESS", "FAILURE", "REVOKED"].includes(polling.data.status)) setUncertain(false); }, [polling.data]);
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
    if (request.current || uncertain) return;
    if (!selectedFile) { setSubmitState({ isSubmitting: false, error: "Please choose an audio file." }); return; }
    const controller = new AbortController(); request.current = controller;
    const current = ++generation.current;
    setJobId(undefined);
    setSubmitState({ isSubmitting: true, error: undefined });

    let definiteRefusal = false;
    try {

      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("strings", "4");
      formData.append("tuning", "standard");

      const response = await fetch(`${API_BASE}/api/v1/jobs`, {
        method: "POST",
        body: formData,
        signal: controller.signal,
      });

      if (!response.ok) {
        definiteRefusal = response.status >= 400 && response.status < 500;
        const detail = await response.text();
        throw new Error(`Failed to create job (${response.status}) ${detail}`.trim());
      }

      const json = (await response.json()) as JobStatus;
      if (!/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(json.job_id)) throw new Error("Invalid job identifier in upload response");
      if (current !== generation.current || controller.signal.aborted) return;
      setJobId(json.job_id);
    } catch (err) {
      if (current !== generation.current || controller.signal.aborted) return;
      setUncertain(!definiteRefusal);
      setSubmitState({
        isSubmitting: false,
        error: err instanceof Error ? err.message : "Unexpected error",
      });
      return;
    } finally { if (current === generation.current) request.current = null; }

    if (current !== generation.current) return;
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
            onChange={(event) => { generation.current++; request.current?.abort(); request.current = null; setJobId(undefined); setSelectedFile(event.target.files?.[0] ?? null); }}
            disabled={submitState.isSubmitting}
          />
        </label>
        <button
          type="submit"
          disabled={submitState.isSubmitting || !selectedFile || uncertain}
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
      {submitState.isSubmitting && <button onClick={() => {
        generation.current++; request.current?.abort(); request.current = null; setUncertain(true);
        setSubmitState({ isSubmitting: false, error: "通信の待機を中断しました。サーバー処理の取消は未対応です。二重実行を避けるため、結果不明時は新規送信せず保存されたジョブを確認してください。" });
      }}>送信の待機を中断（処理取消ではありません）</button>}
      {uncertain && <section aria-label="送信結果の確認"><p>送信結果が不明です。新しい送信を止めています。保存されたジョブIDが分かる場合は同じジョブを確認してください。</p><label>保存ジョブID <input value={lookup} onChange={e => setLookup(e.target.value)} /></label><button onClick={() => {
        if (/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(lookup)) setJobId(lookup);
      }}>保存ジョブを確認</button></section>}
      <p>通常経路のGP5は四分音符のプレビューです。時刻を保持した表示や明示リズムGP5には、完了後の生成MIDIから進んでください。</p>
      {polling.data?.status === "SUCCESS" && midi && <Link to={`/midi-trial/from-job/${polling.data.job_id}?name=${encodeURIComponent(midi)}`}>生成MIDIから原時刻の五線譜・TAB・任意補正へ</Link>}


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
            <Suspense fallback={<p>譜面ビューアを読み込んでいます…</p>}>
              <AlphaTabViewer scoreUrl={scoreUrl} soundFontUrl={ALPHATAB_SOUNDFONT} />
            </Suspense>
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
