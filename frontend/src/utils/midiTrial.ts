export interface MidiTrialSummary {
  trial_id: string; status: string; source_kind: string; original_filename: string;
  input_sha256: string; input_bytes: number; input_note_count: number;
  source_duration_seconds: number; last_note_end_seconds: number;
  storage_clock: { ppq: number; tempo_bpm: number; meter: string; musical_tempo_and_meter_verified: boolean };
  config: Record<string, unknown>;
  gp5: { requested: boolean; status: string; mapped_count: number | null; unmappable: Array<{ source_index: number; midi: number; start: number; end: number; reason: string }> };
  warnings: string[];
  artifacts: Array<{ key: string; kind: string; filename: string; bytes: number; sha256: string; url: string }>;
  error: { code: string; message: string } | null;
}
export function isTrialSummary(value: unknown): value is MidiTrialSummary {
  const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
  const text = (v: unknown): v is string => typeof v === "string";
  const number = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
  const integer = (v: unknown): v is number => number(v) && Number.isInteger(v);
  const sha = (v: unknown): boolean => text(v) && /^[a-f0-9]{64}$/.test(v);
  if (!record(value)) return false;
  const v = value, clock = v.storage_clock, gp5 = v.gp5;
  if (!text(v.trial_id) || !/^[A-Za-z0-9-]+$/.test(v.trial_id) || !["completed", "gp5_blocked_out_of_range", "failed"].includes(String(v.status)) ||
    !["muscriptor-small", "local-midi"].includes(String(v.source_kind)) || !text(v.original_filename) ||
    !sha(v.input_sha256) || !integer(v.input_bytes) || !integer(v.input_note_count) ||
    !number(v.source_duration_seconds) || !number(v.last_note_end_seconds) || !record(v.config) ||
    !record(clock) || !integer(clock.ppq) || clock.ppq === 0 || !number(clock.tempo_bpm) || clock.tempo_bpm === 0 ||
    clock.meter !== "4/4" || clock.musical_tempo_and_meter_verified !== false ||
    !record(gp5) || typeof gp5.requested !== "boolean" ||
    !["not_requested", "written", "unmappable", "failed"].includes(String(gp5.status)) ||
    !(gp5.mapped_count === null || integer(gp5.mapped_count)) || !Array.isArray(gp5.unmappable) ||
    !gp5.unmappable.every((n: unknown) => record(n) && integer(n.source_index) && integer(n.midi) && n.midi <= 127 && number(n.start) && number(n.end) && n.end >= n.start && text(n.reason)) ||
    !Array.isArray(v.warnings) || !v.warnings.every(text) || !Array.isArray(v.artifacts)) return false;
  const keys = ["original-midi", "storage-clock-xml", "canonical-json", "canonical-csv", "source-events", "gp5-preview"];
  if (!v.artifacts.every((a: unknown) => record(a) && text(a.key) && keys.includes(a.key) && text(a.kind) && text(a.filename) && integer(a.bytes) && sha(a.sha256) &&
    text(a.url) && a.url === `/api/v1/midi-trials/${v.trial_id}/files/${a.key}`)) return false;
  return v.error === null || (record(v.error) && text(v.error.code) && text(v.error.message));
}
export function pitchName(midi: number): string {
  return `${["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"][((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`;
}
