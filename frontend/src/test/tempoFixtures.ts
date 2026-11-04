import type { StoredTempoSource, TempoEstimate } from "../utils/tempoEstimate";
export const tempoTrialId = "12345678-1234-4123-8123-123456789012";
export const tempoJobId = "23456789-1234-4123-8123-123456789012";
export const tempoSource: StoredTempoSource = {
  kind: "stored-selection", transcription_job_id: tempoJobId, midi_trial_id: tempoTrialId,
  midi_sha256: "a".repeat(64), audio_sha256: "b".repeat(64), audio_bytes: 640044,
  duration_seconds: 20, source_origin_seconds: 12.25, sample_rate: 16000, channels: 1, format: "WAV", correspondence: "verified-completed-job",
};
export function tempoReport(): TempoEstimate {
  return {
    schema_version: "1.0", source: { ...tempoSource }, status: "candidates", reason_codes: [],
    method: { name: "spectral-flux-autocorrelation-v1", analysis_sample_rate: 8000, hop_seconds: .01, bpm_min: 40, bpm_max: 240, quality_calibration: "uncalibrated" },
    analyzed_intervals: [{ start_seconds: 0, end_seconds: 20 }], coverage: "full",
    candidates: [
      { bpm: 120, relation: "primary", family_id: "pulse-1", periodicity: .72, supporting_windows: 2, adoptable: true },
      { bpm: 60, relation: "half", family_id: "pulse-1", periodicity: null, supporting_windows: 0, adoptable: true },
      { bpm: 240, relation: "double", family_id: "pulse-1", periodicity: null, supporting_windows: 0, adoptable: true },
    ],
    windows: [0, 10].map(start => ({ start_seconds: start, end_seconds: start + 10, status: "periodic", bpm: 120, periodicity: .72, onset_count: 20 })),
    warnings: ["合成解析例です。品質指標は正解確率ではありません。"],
  };
}
export const tempoResponse = (value: unknown, status = 200): Response => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
export const tempoTrial = {
  trial_id: tempoTrialId, status: "completed", source_kind: "muscriptor-small", original_filename: "synthetic.mid",
  input_sha256: tempoSource.midi_sha256, input_bytes: 30, input_note_count: 1, source_duration_seconds: 20, last_note_end_seconds: 1,
  storage_clock: { ppq: 480, tempo_bpm: 60, meter: "4/4", musical_tempo_and_meter_verified: false }, config: {},
  gp5: { requested: false, status: "not_requested", mapped_count: null, unmappable: [] }, artifacts: [], warnings: [], error: null,
};
