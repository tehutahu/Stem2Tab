/** Audio tempo API v1 boundary. It never reads MIDI tempo or changes a clock. */
export interface AudioMetadata {
  audio_sha256: string; audio_bytes: number; duration_seconds: number; sample_rate: number; channels: 1 | 2;
}
export interface StoredTempoSource extends AudioMetadata {
  kind: "stored-selection"; transcription_job_id: string; midi_trial_id: string; midi_sha256: string;
  source_origin_seconds: number; format: "WAV"; correspondence: "verified-completed-job" | "verified-review-preview";
}
export interface UploadedTempoSource extends AudioMetadata {
  kind: "uploaded-audio"; format: "WAV" | "FLAC"; correspondence: "unverified-upload";
}
export type TempoSource = StoredTempoSource | UploadedTempoSource;
export interface TempoInterval { start_seconds: number; end_seconds: number }
export interface TempoWindow extends TempoInterval {
  status: "periodic" | "weak" | "insufficient"; bpm: number | null; periodicity: number | null; onset_count: number;
}
export interface TempoCandidate {
  bpm: number; relation: "primary" | "alternative" | "half" | "double"; family_id: "pulse-1" | "pulse-2";
  periodicity: number | null; supporting_windows: number; adoptable: boolean;
}
export interface TempoEstimate {
  schema_version: "1.0"; source: TempoSource; status: "candidates" | "insufficient_evidence" | "unstable";
  reason_codes: string[];
  method: { name: "spectral-flux-autocorrelation-v1"; analysis_sample_rate: 8000; hop_seconds: 0.01;
    bpm_min: 40; bpm_max: 240; quality_calibration: "uncalibrated" };
  analyzed_intervals: TempoInterval[]; coverage: "full" | "sampled"; candidates: TempoCandidate[]; windows: TempoWindow[]; warnings: string[];
}
export interface TempoAdoption { bpm: string; evidence: string }
export type TempoExpectedSource = StoredTempoSource | {
  kind: "uploaded-audio"; audio_sha256: string; audio_bytes: number; format: "WAV" | "FLAC";
};
export const TEMPO_UPLOAD_BYTES = 32 * 1024 * 1024;
export const isTempoUuid = (value: string): boolean => /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const number = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const integer = (value: unknown, min: number, max: number): value is number => number(value) && Number.isSafeInteger(value) && value >= min && value <= max;
const sha = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const safeText = (value: unknown): value is string => typeof value === "string" && value.length <= 500 && !/[\u0000-\u001f\u007f]/.test(value);
const invalid = (): never => { throw new Error("BPM候補の応答・音声の対応情報が不正です。入力は変更していません。"); };
const tolerance = 0.000001;
function metadata(value: Record<string, unknown>, maxBytes: number, maxSeconds: number): AudioMetadata {
  if (!sha(value.audio_sha256) || !integer(value.audio_bytes, 1, maxBytes) || !number(value.duration_seconds) || value.duration_seconds <= 0 || value.duration_seconds > maxSeconds ||
    !integer(value.sample_rate, 8000, 96000) || (value.channels !== 1 && value.channels !== 2)) return invalid();
  return { audio_sha256: value.audio_sha256, audio_bytes: value.audio_bytes, duration_seconds: value.duration_seconds, sample_rate: value.sample_rate, channels: value.channels };
}
function parseSource(value: unknown): TempoSource {
  if (!record(value)) return invalid();
  if (value.kind === "stored-selection") {
    if (typeof value.transcription_job_id !== "string" || !isTempoUuid(value.transcription_job_id) || typeof value.midi_trial_id !== "string" || !isTempoUuid(value.midi_trial_id) || !sha(value.midi_sha256) ||
      !number(value.source_origin_seconds) || value.source_origin_seconds < 0 || value.format !== "WAV" || (value.correspondence !== "verified-completed-job" && value.correspondence !== "verified-review-preview")) return invalid();
    return { kind: value.kind, ...metadata(value, 536870912, 600), transcription_job_id: value.transcription_job_id, midi_trial_id: value.midi_trial_id, midi_sha256: value.midi_sha256,
      source_origin_seconds: value.source_origin_seconds, format: value.format, correspondence: value.correspondence };
  }
  if (value.kind === "uploaded-audio" && (value.format === "WAV" || value.format === "FLAC") && value.correspondence === "unverified-upload") {
    return { kind: value.kind, ...metadata(value, TEMPO_UPLOAD_BYTES, 120), format: value.format, correspondence: value.correspondence };
  }
  return invalid();
}
/** A route's job hint is never proof that audio belongs to the active saved MIDI. */
export function parseStoredTempoSource(value: unknown, expected: { trialId: string; midiSha256: string; jobId: string }): StoredTempoSource {
  const source = parseSource(value);
  if (source.kind !== "stored-selection" || source.midi_trial_id !== expected.trialId || source.midi_sha256 !== expected.midiSha256 || source.transcription_job_id !== expected.jobId) return invalid();
  return source;
}
function parseInterval(value: unknown, duration: number): TempoInterval {
  if (!record(value) || !number(value.start_seconds) || !number(value.end_seconds) || value.start_seconds < 0 || value.end_seconds <= value.start_seconds || value.end_seconds > duration + tolerance) return invalid();
  return { start_seconds: value.start_seconds, end_seconds: value.end_seconds };
}
const ordered = (intervals: TempoInterval[]): boolean => intervals.every((interval, index) => index === 0 || interval.start_seconds >= intervals[index - 1].end_seconds - tolerance);
/** Parse once, then bind estimates to exactly the selected audio and MIDI. */
export function parseTempoEstimate(value: unknown, expected: TempoExpectedSource): TempoEstimate {
  if (!record(value) || value.schema_version !== "1.0" || (value.status !== "candidates" && value.status !== "insufficient_evidence" && value.status !== "unstable") ||
    !Array.isArray(value.reason_codes) || value.reason_codes.length > 12 || !value.reason_codes.every((code: unknown): code is string => typeof code === "string" && /^[a-z0-9_]{1,64}$/.test(code)) ||
    !Array.isArray(value.warnings) || value.warnings.length > 12 || !value.warnings.every(safeText) || !record(value.method)) return invalid();
  const method = value.method;
  if (method.name !== "spectral-flux-autocorrelation-v1" || method.analysis_sample_rate !== 8000 || method.hop_seconds !== .01 || method.bpm_min !== 40 || method.bpm_max !== 240 || method.quality_calibration !== "uncalibrated") return invalid();
  const source = parseSource(value.source);
  if (source.kind !== expected.kind || source.audio_sha256 !== expected.audio_sha256 || source.audio_bytes !== expected.audio_bytes || source.format !== expected.format) return invalid();
  if (expected.kind === "stored-selection" && (source.kind !== "stored-selection" || JSON.stringify(source) !== JSON.stringify(parseStoredTempoSource(expected, { trialId: expected.midi_trial_id, midiSha256: expected.midi_sha256, jobId: expected.transcription_job_id })))) return invalid();
  if ((value.coverage !== "full" && value.coverage !== "sampled") || !Array.isArray(value.analyzed_intervals) || value.analyzed_intervals.length < 1 || value.analyzed_intervals.length > 3) return invalid();
  const intervals = value.analyzed_intervals.map(interval => parseInterval(interval, source.duration_seconds));
  const total = intervals.reduce((sum, interval) => sum + interval.end_seconds - interval.start_seconds, 0);
  if (!ordered(intervals) || intervals.some(interval => interval.end_seconds - interval.start_seconds > 20 + tolerance) || total > 60 + tolerance ||
    (value.coverage === "full" ? Math.abs(total - source.duration_seconds) > tolerance : total >= source.duration_seconds - tolerance)) return invalid();
  if (!Array.isArray(value.windows) || value.windows.length < 1 || value.windows.length > 6) return invalid();
  const windows: TempoWindow[] = value.windows.map(window => {
    const interval = parseInterval(window, source.duration_seconds);
    if (!record(window) || (window.status !== "periodic" && window.status !== "weak" && window.status !== "insufficient") || !integer(window.onset_count, 0, 3000) ||
      !(window.periodicity === null || number(window.periodicity) && window.periodicity >= 0 && window.periodicity <= 1) || !(window.bpm === null || number(window.bpm)) ||
      (window.status === "periodic" ? !number(window.bpm) || window.bpm < 40 || window.bpm > 240 : window.bpm !== null) ||
      !intervals.some(analyzed => interval.start_seconds >= analyzed.start_seconds - tolerance && interval.end_seconds <= analyzed.end_seconds + tolerance)) return invalid();
    return { ...interval, status: window.status, bpm: window.bpm, periodicity: window.periodicity, onset_count: window.onset_count };
  });
  if (!ordered(windows) || !Array.isArray(value.candidates) || value.candidates.length > 6 || (value.status === "candidates" ? value.candidates.length < 1 : value.candidates.length !== 0)) return invalid();
  const candidates: TempoCandidate[] = value.candidates.map(candidate => {
    if (!record(candidate) || !number(candidate.bpm) || candidate.bpm < 20 || candidate.bpm > 480 || Math.abs(candidate.bpm * 1000 - Math.round(candidate.bpm * 1000)) > tolerance ||
      (candidate.relation !== "primary" && candidate.relation !== "alternative" && candidate.relation !== "half" && candidate.relation !== "double") || (candidate.family_id !== "pulse-1" && candidate.family_id !== "pulse-2") ||
      !integer(candidate.supporting_windows, 0, windows.length) || !(candidate.periodicity === null || number(candidate.periodicity)) || candidate.adoptable !== (candidate.bpm >= 20 && candidate.bpm <= 300) ||
      (candidate.relation === "primary" || candidate.relation === "alternative" ? !number(candidate.periodicity) || candidate.periodicity < 0 || candidate.periodicity > 1 || candidate.supporting_windows < 1 : candidate.periodicity !== null || candidate.supporting_windows !== 0)) return invalid();
    return { bpm: candidate.bpm, relation: candidate.relation, family_id: candidate.family_id, periodicity: candidate.periodicity, supporting_windows: candidate.supporting_windows, adoptable: candidate.adoptable };
  });
  const primary = candidates.find(candidate => candidate.relation === "primary" && candidate.family_id === "pulse-1");
  if (candidates.length && (!primary || new Set(candidates.map(candidate => `${candidate.family_id}:${candidate.relation}`)).size !== candidates.length)) return invalid();
  for (const candidate of candidates) {
    const measured = candidate.relation === "primary" || candidate.relation === "alternative";
    const base = candidates.find(item => item.family_id === candidate.family_id && item.relation === (candidate.family_id === "pulse-1" ? "primary" : "alternative"));
    if (!base || base.bpm < 40 || base.bpm > 240 || (measured && candidate.relation !== (candidate.family_id === "pulse-1" ? "primary" : "alternative")) ||
      Math.abs(candidate.bpm - base.bpm * (candidate.relation === "half" ? .5 : candidate.relation === "double" ? 2 : 1)) > .001) return invalid();
  }
  return { schema_version: "1.0", source, status: value.status, reason_codes: value.reason_codes,
    method: { name: method.name, analysis_sample_rate: method.analysis_sample_rate, hop_seconds: method.hop_seconds, bpm_min: method.bpm_min, bpm_max: method.bpm_max, quality_calibration: method.quality_calibration },
    analyzed_intervals: intervals, coverage: value.coverage, candidates, windows, warnings: value.warnings };
}
/** Source-bound evidence for an explicit manual edit, never a probability. */
export function tempoAdoption(estimate: TempoEstimate, candidate: TempoCandidate): TempoAdoption {
  const source = estimate.source;
  const intervals = estimate.analyzed_intervals.map(interval => `${interval.start_seconds}–${interval.end_seconds}s`).join(",");
  const provenance = source.kind === "stored-selection" ? `job=${source.transcription_job_id};MIDI=${source.midi_sha256}` : "アップロード音声・MIDI対応未検証";
  return { bpm: String(candidate.bpm), evidence: `音声BPM候補 ${candidate.bpm};${candidate.family_id}/${candidate.relation};${estimate.method.name};audio=${source.audio_sha256};${provenance};解析=${intervals};品質未校正・確率ではない;拍子/小節頭未推定`.slice(0, 500) };
}
