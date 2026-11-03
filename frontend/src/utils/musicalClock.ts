export interface MusicalClock {
  bpm: number; numerator: number; denominator: 2 | 4 | 8 | 16;
  origin_seconds: number; evidence: string; provenance: "explicit-user-input";
}
export interface MusicalClockProof { bpm: number; events: Array<[number, number, number]>; horizonTicks: number }
export interface MusicalClockSelection { url: string; proof: MusicalClockProof }
export interface MusicalClockDerivative {
  musicxml: string; musicxml_sha256: string;
  report: {
    clock: MusicalClock; source_sha256: string; storage_bpm: number; storage_meter: "4/4";
    estimation_status: "not-estimated"; confidence: null; ambiguity_bpm: number[];
    note_count: number; divisions: number; horizon_seconds: string; quantization: "none";
    preview_events: Array<[number, number, number]> | null; preview_horizon_ticks: number | null;
    preview_eligible: boolean; preview_reason: string | null; scope: string;
  };
}
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const sha = (v: unknown): v is string => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const denominator = (v: unknown): v is MusicalClock["denominator"] => v === 2 || v === 4 || v === 8 || v === 16;

/** Validate the derivative at the network boundary before creating any blob URL. */
export function isMusicalClockDerivative(value: unknown): value is MusicalClockDerivative {
  if (!record(value) || typeof value.musicxml !== "string" || !value.musicxml.startsWith("<?xml") || !sha(value.musicxml_sha256) || !record(value.report)) return false;
  const r = value.report, c = r.clock, horizon = r.preview_horizon_ticks;
  return record(c) && finite(c.bpm) && c.bpm >= 20 && c.bpm <= 300 && finite(c.numerator) && Number.isInteger(c.numerator) && c.numerator >= 1 && c.numerator <= 12 && denominator(c.denominator) && finite(c.origin_seconds) && c.origin_seconds >= 0 && c.origin_seconds <= 600 && typeof c.evidence === "string" && c.provenance === "explicit-user-input" &&
    ((r.preview_eligible === false && r.preview_events === null && r.preview_horizon_ticks === null) ||
      (r.preview_eligible === true && finite(r.preview_horizon_ticks) && Number.isSafeInteger(r.preview_horizon_ticks) && r.preview_horizon_ticks > 0 && Array.isArray(r.preview_events) && r.preview_events.every((e: unknown) => Array.isArray(e) && e.length === 3 && e.every((v: unknown) => finite(v) && Number.isSafeInteger(v) && v >= 0) && e[0] <= 127 && e[2] > e[1] && finite(horizon) && e[2] <= horizon))) &&
    sha(r.source_sha256) && finite(r.storage_bpm) && r.storage_bpm > 0 && r.storage_meter === "4/4" && r.estimation_status === "not-estimated" && r.confidence === null && Array.isArray(r.ambiguity_bpm) && r.ambiguity_bpm.length === 2 && r.ambiguity_bpm.every(finite) && finite(r.note_count) && Number.isInteger(r.note_count) && r.note_count >= 0 && finite(r.divisions) && Number.isInteger(r.divisions) && r.divisions > 0 && typeof r.horizon_seconds === "string" && /^\d+(\/\d+)?$/.test(r.horizon_seconds) && r.quantization === "none" && typeof r.preview_eligible === "boolean" && (r.preview_reason === null || typeof r.preview_reason === "string") && typeof r.scope === "string";
}

/** Explicit blank fields remain unknown; they must never become a default tempo. */
export function readMusicalClock(bpm: string, meter: string, origin: string, evidence: string): MusicalClock | null {
  const match = /^(\d+)\/(2|4|8|16)$/.exec(meter);
  if (!/^(\d+(\.\d{1,3})?|\.\d{1,3})$/.test(bpm) || !/^(\d+(\.\d{1,6})?|\.\d{1,6})$/.test(origin) || !match) return null;
  const b = Number(bpm), n = Number(match[1]), d = Number(match[2]), o = Number(origin);
  if (b < 20 || b > 300 || n < 1 || n > 12 || o < 0 || o > 600 || !denominator(d) || evidence.length > 500) return null;
  return { bpm: b, numerator: n, denominator: d, origin_seconds: o, evidence, provenance: "explicit-user-input" };
}
