import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import MusicalClockSettings from "../components/MusicalClockSettings";
import { readMusicalClock, isMusicalClockDerivative, type MusicalClockDerivative } from "../utils/musicalClock";
import type { MidiTrialSummary } from "../utils/midiTrial";

const trial: MidiTrialSummary = {
  trial_id: "1234", status: "completed", source_kind: "local-midi", original_filename: "synthetic.mid",
  input_sha256: "a".repeat(64), input_bytes: 30, input_note_count: 1, source_duration_seconds: 4, last_note_end_seconds: 1,
  storage_clock: { ppq: 480, tempo_bpm: 60, meter: "4/4", musical_tempo_and_meter_verified: false }, config: {},
  gp5: { requested: false, status: "not_requested", mapped_count: null, unmappable: [] }, artifacts: [], warnings: [], error: null,
};
function derivative(eligible = true): MusicalClockDerivative {
  return {
    musicxml: "<?xml version=\"1.0\"?><score-partwise/>", musicxml_sha256: "b".repeat(64),
    report: { clock: { bpm: 120, numerator: 3, denominator: 4, origin_seconds: .25, evidence: "synthetic pulse", provenance: "explicit-user-input" },
      source_sha256: trial.input_sha256, storage_bpm: 60, storage_meter: "4/4", estimation_status: "not-estimated", confidence: null,
      ambiguity_bpm: [60,240], note_count: 1, divisions: 480, horizon_seconds: "4", quantization: "none",
      preview_eligible: eligible, preview_reason: eligible ? null : "960 ticks cannot represent input exactly",
      preview_events: eligible ? [[28,0,1920]] : null, preview_horizon_ticks: eligible ? 7680 : null, scope: "Synthetic exact source times" },
  };
}
function fill(): void {
  fireEvent.change(screen.getByLabelText("音楽的BPM（四分音符）"), { target: { value: "120" } });
  fireEvent.change(screen.getByLabelText("音楽的拍子"), { target: { value: "3/4" } });
  fireEvent.change(screen.getByLabelText(/拍原点（/), { target: { value: ".25" } });
  fireEvent.change(screen.getByLabelText(/設定の根拠メモ/), { target: { value: "synthetic pulse" } });
}
function setup(body: unknown = derivative()) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body }));
  const createObjectURL = vi.fn().mockReturnValueOnce("blob:xml").mockReturnValueOnce("blob:report");
  const revokeObjectURL = vi.fn(); vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL }));
  const onSelect = vi.fn();
  return { ...render(<MusicalClockSettings trial={trial} onSelect={onSelect} />), onSelect, createObjectURL, revokeObjectURL };
}
afterEach(() => vi.unstubAllGlobals());
it("keeps every musical field unknown until explicit input, and makes no automatic estimate request", () => {
  setup();
  expect(screen.getByLabelText("音楽的BPM（四分音符）")).toHaveValue(null);
  expect(screen.getByLabelText("音楽的拍子")).toHaveValue("");
  expect(screen.getByLabelText(/拍原点（/)).toHaveValue(null);
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.getByText(/未設定・未推定/)).toBeVisible();
});
it("posts explicit provenance, offers separate downloads and selects only a verified engraved preview", async () => {
  const ui = setup(); fill();
  fireEvent.click(screen.getByRole("button", { name: "入力した音楽的クロックで別版を作成" }));
  expect(await screen.findByRole("link", { name: "別版MusicXMLを保存" })).toHaveAttribute("href", "blob:xml");
  const args = vi.mocked(fetch).mock.calls[0];
  expect(args[0]).toBe("/api/v1/musical-clock/1234/derive");
  expect(JSON.parse(String(args[1]?.body))).toEqual({ input_sha256: trial.input_sha256, clock: derivative().report.clock });
  expect(screen.getByText(/半テンポ 60/)).toHaveTextContent("倍テンポ 240");
  expect(ui.onSelect).toHaveBeenCalledWith(null);
  fireEvent.click(screen.getByRole("button", { name: /この別版を通常譜刻で表示/ }));
  expect(ui.onSelect).toHaveBeenLastCalledWith({ url: "blob:xml", proof: { bpm: 120, events: [[28,0,1920]], horizonTicks: 7680 } });
  ui.unmount(); expect(ui.revokeObjectURL).toHaveBeenCalledTimes(2);
});
it("offers exact downloads but withholds rounded playback when the browser clock is incompatible", async () => {
  setup(derivative(false)); fill(); fireEvent.click(screen.getByRole("button", { name: "入力した音楽的クロックで別版を作成" }));
  expect(await screen.findByRole("link", { name: "別版MusicXMLを保存" })).toBeVisible();
  expect(screen.queryByRole("button", { name: /この別版を通常譜刻/ })).toBeNull();
  expect(screen.getByText(/近似再生は行わず/)).toBeVisible();
});
it("rejects malformed and mismatched derivative responses before creating URLs", async () => {
  const body=derivative(); body.report.source_sha256="f".repeat(64);
  const ui=setup(body);fill();fireEvent.click(screen.getByRole("button",{name:"入力した音楽的クロックで別版を作成"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("別版を作成できません");
  expect(ui.createObjectURL).not.toHaveBeenCalled();
});
it("aborts an in-flight derivative when unmounted", async () => {
  const ui=setup();vi.mocked(fetch).mockImplementation(()=>new Promise(()=>{}));
  fill();fireEvent.click(screen.getByRole("button",{name:"入力した音楽的クロックで別版を作成"}));
  await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));
  const signal=vi.mocked(fetch).mock.calls[0][1]?.signal;ui.unmount();expect(signal?.aborted).toBe(true);
});
it("validates explicit precision, never converts blank fields to zero, and rejects fabricated confidence", () => {
  expect(readMusicalClock("","4/4","0","")).toBeNull();
  expect(readMusicalClock("120","4/4","","")).toBeNull();
  expect(readMusicalClock("120.0001","4/4","0","")).toBeNull();
  expect(readMusicalClock("120","3/7","0","")).toBeNull();
  expect(isMusicalClockDerivative(derivative())).toBe(true);
  const body: unknown={...derivative(),report:{...derivative().report,confidence:.99}};
  expect(isMusicalClockDerivative(body)).toBe(false);
});
