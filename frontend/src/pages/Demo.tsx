import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import AlphaTabViewer from "../components/AlphaTabViewer";
import Fretboard, { type FretboardNote } from "../components/Fretboard";
import { ALPHATAB_SOUNDFONT, API_BASE } from "../config";
import { useJobPolling } from "../hooks/useJobPolling";
import { ALPHATAB_SUPPORTED_LABEL, listAlphaTabScores, pickAlphaTabScore } from "../utils/alphaTab";

type PlayerState = "stopped" | "playing" | "paused";

const AUDIO_EXT_REGEX = /\.(wav|mp3|ogg|flac|opus|m4a)$/i;

function Demo(): JSX.Element {
  const params = useParams<{ jobId: string }>();
  const jobId = params.jobId;
  const polling = useJobPolling(jobId, 2500);

  const files = polling.data?.files ?? [];
  const alphaTabScores = useMemo(() => listAlphaTabScores(files), [files]);
  const [selectedScore, setSelectedScore] = useState<string | undefined>(undefined);
  useEffect(() => {
    setSelectedScore((prev) => (prev && alphaTabScores.includes(prev) ? prev : alphaTabScores[0]));
  }, [alphaTabScores]);
  const midi = useMemo(() => files.find((name) => name.toLowerCase().endsWith(".mid")), [files]);
  const scoreFile = selectedScore ?? pickAlphaTabScore(files);
  const audioCandidates = useMemo(
    () => files.filter((name) => AUDIO_EXT_REGEX.test(name)),
    [files],
  );

  const [selectedAudio, setSelectedAudio] = useState<string | undefined>(undefined);
  const [activeNotes, setActiveNotes] = useState<FretboardNote[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    setSelectedAudio((prev) => (audioCandidates.includes(prev ?? "") ? prev : audioCandidates[0]));
  }, [audioCandidates]);

  useEffect(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    if (selectedAudio) {
      audioRef.current = new Audio(`${API_BASE}/api/v1/files/${jobId}?name=${encodeURIComponent(selectedAudio)}`);
    }
  }, [selectedAudio, jobId]);

  if (!jobId) {
    return (
      <section>
        <p>Job ID が指定されていません。</p>
        <Link to="/">アップロードに戻る</Link>
      </section>
    );
  }

  const artifactUrl = (name: string): string =>
    `${API_BASE}/api/v1/files/${jobId}?name=${encodeURIComponent(name)}`;

  const handlePlayerStateChange = useCallback((state: PlayerState): void => {
    const audio = audioRef.current;
    if (!audio) return;
    if (state === "playing") {
      void audio.play().catch(() => undefined);
    } else if (state === "paused") {
      audio.pause();
    } else if (state === "stopped") {
      audio.pause();
      audio.currentTime = 0;
    }
  }, []);

  return (
    <section style={{ display: "grid", gap: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h2 style={{ margin: 0 }}>デモモード</h2>
          <p style={{ margin: 0, color: "#4b5563" }}>ジョブID: {jobId}</p>
          <p style={{ margin: 0, color: "#4b5563" }}>
            ステータス: {polling.data?.status ?? "読み込み中"}{" "}
            {polling.data?.progress !== undefined && `(${polling.data.progress}%)`}
          </p>
        </div>
        <Link to="/" style={{ color: "#2563eb", textDecoration: "underline" }}>
          アップロードに戻る
        </Link>
      </div>

      {polling.error && <p style={{ color: "#dc2626" }}>取得エラー: {polling.error}</p>}
      {polling.data?.error && <p style={{ color: "#dc2626" }}>ジョブエラー: {polling.data.error}</p>}

      <div>
        <h3 style={{ marginBottom: "0.5rem" }}>成果物</h3>
        {files.length === 0 && <p>まだ生成されたファイルはありません。</p>}
        {files.length > 0 && (
          <ul>
            {files.map((name) => (
              <li key={name}>
                <a href={artifactUrl(name)} target="_blank" rel="noreferrer">
                  {name}
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>

      {!scoreFile && files.length > 0 ? (
        <p style={{ color: "#dc2626" }}>
          AlphaTab対応のスコア({ALPHATAB_SUPPORTED_LABEL})がまだ生成されていません。
          {midi ? " 生成済みのMIDIはダウンロードできます (AlphaTabは未対応)。" : ""}
        </p>
      ) : null}

      {scoreFile && (
        <div style={{ display: "grid", gap: "1rem" }}>
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
          {audioCandidates.length > 0 && (
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <label>
                オーディオ切替:{" "}
                <select
                  value={selectedAudio ?? ""}
                  onChange={(event) => setSelectedAudio(event.target.value || undefined)}
                >
                  {audioCandidates.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}

          <AlphaTabViewer
            scoreUrl={artifactUrl(scoreFile)}
            soundFontUrl={ALPHATAB_SOUNDFONT}
            onActiveNotesChange={setActiveNotes}
            onPlayerStateChange={handlePlayerStateChange}
          />

          <div>
            <h3 style={{ marginBottom: "0.5rem" }}>フレットボード</h3>
            <Fretboard activeNotes={activeNotes} strings={4} frets={12} />
          </div>
        </div>
      )}
    </section>
  );
}

export default Demo;


