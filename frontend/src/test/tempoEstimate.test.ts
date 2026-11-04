import { expect, it } from "vitest";
import { parseStoredTempoSource, parseTempoEstimate, tempoAdoption } from "../utils/tempoEstimate";
import { tempoJobId, tempoReport, tempoSource, tempoTrialId } from "./tempoFixtures";
const expected = { trialId: tempoTrialId, jobId: tempoJobId, midiSha256: tempoSource.midi_sha256 };
it("binds a verified audio descriptor to the exact saved MIDI and normal or review source", () => {
  expect(parseStoredTempoSource(tempoSource, expected)).toEqual(tempoSource);
  expect(parseStoredTempoSource({ ...tempoSource, correspondence: "verified-review-preview" }, expected).correspondence).toBe("verified-review-preview");
  for (const mismatch of [{ midi_trial_id: tempoJobId }, { transcription_job_id: tempoTrialId }, { midi_sha256: "c".repeat(64) }, { audio_sha256: "bogus" }, { kind: "uploaded-audio" }, { duration_seconds: 601 }, { sample_rate: 192000 }]) {
    expect(() => parseStoredTempoSource({ ...tempoSource, ...mismatch }, expected)).toThrow();
  }
});
it("accepts one measured pulse family and arithmetic octave alternatives without invented quality", () => {
  const result = parseTempoEstimate(tempoReport(), tempoSource);
  expect(result.candidates.map(candidate => candidate.bpm)).toEqual([120, 60, 240]);
  expect(result.candidates[1].periodicity).toBeNull();
  const adoption = tempoAdoption(result, result.candidates[0]);
  expect(Object.keys(adoption)).toEqual(["bpm", "evidence"]);
  expect(adoption.evidence).toContain(tempoSource.audio_sha256);
  expect(adoption.evidence).toContain("品質未校正");
  expect(adoption.evidence).toContain("拍子/小節頭未推定");
  expect(adoption.evidence.length).toBeLessThanOrEqual(500);
});
it.each(["insufficient_evidence", "unstable"])("keeps %s diagnostics unselectable", status => {
  const result = parseTempoEstimate({ ...tempoReport(), status, candidates: [], reason_codes: ["weak_periodicity"] }, tempoSource);
  expect(result.candidates).toEqual([]);
  expect(result.windows).toHaveLength(2);
});
it("accepts explicit sampled coverage while rejecting a false full-source claim", () => {
  const source = { ...tempoSource, duration_seconds: 120 };
  const value = { ...tempoReport(), source, coverage: "sampled" };
  expect(parseTempoEstimate(value, source).coverage).toBe("sampled");
  expect(() => parseTempoEstimate({ ...value, coverage: "full" }, source)).toThrow();
});
it("verifies standalone upload bytes, format and SHA without inventing MIDI correspondence", () => {
  const source = { kind: "uploaded-audio", audio_sha256: "c".repeat(64), audio_bytes: 64, duration_seconds: 20, sample_rate: 16000, channels: 1, format: "FLAC", correspondence: "unverified-upload" };
  const expectedUpload = { kind: "uploaded-audio", audio_sha256: source.audio_sha256, audio_bytes: 64, format: "FLAC" } satisfies Parameters<typeof parseTempoEstimate>[1];
  const result = parseTempoEstimate({ ...tempoReport(), source }, expectedUpload);
  expect(tempoAdoption(result, result.candidates[0]).evidence).toContain("MIDI対応未検証");
  expect(() => parseTempoEstimate({ ...tempoReport(), source: { ...source, audio_sha256: "d".repeat(64) } }, expectedUpload)).toThrow();
  expect(() => parseTempoEstimate({ ...tempoReport(), source: { ...source, audio_bytes: 65 } }, expectedUpload)).toThrow();
  expect(() => parseTempoEstimate({ ...tempoReport(), source: { ...source, correspondence: "verified-completed-job" } }, expectedUpload)).toThrow();
});
it("rejects changed stored audio metadata even if source hashes still match", () => {
  expect(() => parseTempoEstimate({ ...tempoReport(), source: { ...tempoSource, source_origin_seconds: 0 } }, tempoSource)).toThrow();
});
it.each([
  { schema_version: "2" },
  { status: "insufficient_evidence" },
  { coverage: "guessed" },
  { reason_codes: ["bad code"] },
  { warnings: ["private\npath"] },
  { candidates: [{ ...tempoReport().candidates[0], family_id: "other" }] },
  { candidates: [{ ...tempoReport().candidates[0], periodicity: .99, supporting_windows: 7 }] },
  { candidates: [{ ...tempoReport().candidates[1], periodicity: .8 }, tempoReport().candidates[0]] },
  { candidates: [{ ...tempoReport().candidates[1], bpm: 70 }, tempoReport().candidates[0]] },
  { candidates: [tempoReport().candidates[0], tempoReport().candidates[0]] },
  { analyzed_intervals: [{ start_seconds: 0, end_seconds: 10 }, { start_seconds: 9, end_seconds: 19 }] },
  { windows: [{ ...tempoReport().windows[0], end_seconds: 21 }] },
  { windows: [{ ...tempoReport().windows[0], status: "weak", bpm: 120 }] },
])("rejects malformed, misleading or inconsistent analysis: %j", change => {
  expect(() => parseTempoEstimate({ ...tempoReport(), ...change }, tempoSource)).toThrow();
});
it("displays an octave interpretation outside the manual range without permitting adoption", () => {
  const candidates = tempoReport().candidates.map(candidate => ({ ...candidate, bpm: candidate.bpm * 1.5, adoptable: candidate.bpm * 1.5 <= 300 }));
  expect(parseTempoEstimate({ ...tempoReport(), candidates }, tempoSource).candidates[2]).toMatchObject({ bpm: 360, adoptable: false });
  expect(() => parseTempoEstimate({ ...tempoReport(), candidates: candidates.map(candidate => ({ ...candidate, adoptable: true })) }, tempoSource)).toThrow();
});
it("keeps a measured non-octave pulse hypothesis and validates each family's octave relations independently", () => {
  const report = tempoReport();
  const candidates = [...report.candidates, ...report.candidates.map(candidate => ({ ...candidate,
    family_id: "pulse-2", relation: candidate.relation === "primary" ? "alternative" : candidate.relation,
    bpm: candidate.bpm * 5 / 6,
  }))];
  const result = parseTempoEstimate({ ...report, candidates, reason_codes: ["multiple_pulse_hypotheses"] }, tempoSource);
  expect(result.candidates.map(candidate => candidate.bpm)).toEqual([120, 60, 240, 100, 50, 200]);
  expect(tempoAdoption(result, result.candidates[3]).evidence).toContain("pulse-2/alternative");
  expect(() => parseTempoEstimate({ ...report, candidates: candidates.map(candidate => candidate.relation === "alternative" ? { ...candidate, family_id: "pulse-1" } : candidate) }, tempoSource)).toThrow();
});
