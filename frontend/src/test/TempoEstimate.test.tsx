import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import TempoEstimatePanel from "../components/TempoEstimate";
import { tempoJobId, tempoReport, tempoResponse, tempoSource, tempoTrial, tempoTrialId } from "./tempoFixtures";
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const sourceSelect = () => screen.getByLabelText("推定する音声");
const estimateButton = () => screen.getByRole("button", { name: "選択した音声からBPM候補を推定" });
function mount(sourceJobId: string | undefined = tempoJobId) {
  const onAdopt = vi.fn(), onInvalidate = vi.fn();
  return { onAdopt, onInvalidate, ...render(<TempoEstimatePanel trial={tempoTrial} sourceJobId={sourceJobId} onAdopt={onAdopt} onInvalidate={onInvalidate} />) };
}
async function verifySource(): Promise<void> {
  fireEvent.change(sourceSelect(), { target: { value: "stored" } });
  fireEvent.click(screen.getByRole("button", { name: "保存音声の対応を確認" }));
  await screen.findByText(/対応確認済み/);
}
it("makes no source or estimate request on mount or source selection, then verifies exact correspondence explicitly", async () => {
  const fetcher = vi.fn().mockResolvedValue(tempoResponse(tempoSource)); vi.stubGlobal("fetch", fetcher);
  const ui = mount(); expect(fetcher).not.toHaveBeenCalled();
  fireEvent.change(sourceSelect(), { target: { value: "stored" } }); expect(fetcher).not.toHaveBeenCalled();
  expect(estimateButton()).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存音声の対応を確認" })); await screen.findByText(/対応確認済み/);
  expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/tempo-estimates/sources?midi_trial_id=${tempoTrialId}&transcription_job_id=${tempoJobId}`);
  expect(estimateButton()).toBeEnabled(); expect(ui.onAdopt).not.toHaveBeenCalled();
});
it("submits only a verified descriptor and adopts a candidate only after an explicit click", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(tempoResponse(tempoSource)).mockResolvedValueOnce(tempoResponse(tempoReport())); vi.stubGlobal("fetch", fetcher);
  const ui = mount(); await verifySource();
  fireEvent.click(estimateButton()); fireEvent.click(estimateButton());
  await screen.findByText("BPM候補あり・未反映");
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual(tempoSource); expect(fetcher).toHaveBeenCalledTimes(2);
  expect(ui.onAdopt).not.toHaveBeenCalled();
  expect(screen.getByText(/品質指標は未校正の周期性統計/)).toBeVisible();
  expect(screen.getByText(/解析した区間/)).toHaveTextContent("0〜20");
  expect(screen.getByRole("link", { name: "音声BPM推定レポートを保存" })).toHaveAttribute("download", "audio-tempo-estimate.json");
  fireEvent.click(screen.getByRole("button", { name: /^60 BPMを入力候補に選ぶ/ }));
  expect(ui.onAdopt).toHaveBeenCalledWith(expect.objectContaining({ bpm: "60", evidence: expect.stringContaining(tempoSource.audio_sha256) }));
  expect(fetcher).toHaveBeenCalledTimes(2);
});
it.each(["insufficient_evidence", "unstable"])("shows %s separately without selectable BPM", async status => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(tempoResponse(tempoSource)).mockResolvedValueOnce(tempoResponse({ ...tempoReport(), status, candidates: [], reason_codes: ["weak_periodicity"] })));
  const ui = mount(); await verifySource(); fireEvent.click(estimateButton());
  await screen.findByText(status === "unstable" ? "区間間で不安定・一定BPMは選べません" : "根拠不足・BPMは選べません");
  expect(screen.queryByRole("button", { name: /BPMを入力候補に選ぶ/ })).toBeNull(); expect(ui.onAdopt).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "音声BPM推定レポートを保存" })).toBeVisible();
});
it("rejects wrong audio hashes after estimation and requires verifying stored audio again", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(tempoResponse(tempoSource)).mockResolvedValueOnce(tempoResponse({ ...tempoReport(), source: { ...tempoSource, audio_sha256: "f".repeat(64) } })));
  const ui = mount(); await verifySource(); fireEvent.click(estimateButton());
  expect(await screen.findByRole("alert")).toHaveTextContent("対応情報が不正"); expect(estimateButton()).toBeDisabled();
  expect(ui.onAdopt).not.toHaveBeenCalled(); expect(screen.queryByRole("button", { name: /BPMを入力候補に選ぶ/ })).toBeNull();
});
it("aborts and ignores a late estimate after source replacement, cancellation, or unmount", async () => {
  let finish: ((response: Response) => void) | undefined;
  const fetcher = vi.fn().mockResolvedValueOnce(tempoResponse(tempoSource)).mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; })); vi.stubGlobal("fetch", fetcher);
  const ui = mount(); await verifySource(); fireEvent.click(estimateButton());
  await screen.findByText("音声のBPM候補を推定中…");
  const signal = fetcher.mock.calls[1][1].signal;
  fireEvent.change(sourceSelect(), { target: { value: "upload" } }); expect(signal.aborted).toBe(true);
  await act(async () => { finish?.(tempoResponse(tempoReport())); });
  expect(screen.queryByText("BPM候補あり・未反映")).toBeNull(); expect(ui.onAdopt).not.toHaveBeenCalled();
  fetcher.mockResolvedValueOnce(tempoResponse(tempoSource)).mockImplementationOnce(() => new Promise(() => {}));
  await verifySource(); fireEvent.click(estimateButton());
  fireEvent.click(screen.getByRole("button", { name: "推定の待機を中断" }));
  expect(fetcher.mock.calls[3][1].signal.aborted).toBe(true); expect(screen.getByText(/自動再実行はしません/)).toBeVisible();
  fetcher.mockImplementationOnce(() => new Promise(() => {})); fireEvent.click(estimateButton());
  ui.unmount(); expect(fetcher.mock.calls[4][1].signal.aborted).toBe(true);
});
it("clears old candidates when only the source-job hint changes and never requests another source on its own", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(tempoResponse(tempoSource)).mockResolvedValueOnce(tempoResponse(tempoReport())); vi.stubGlobal("fetch", fetcher);
  const ui = mount(); await verifySource(); fireEvent.click(estimateButton()); await screen.findByText("BPM候補あり・未反映");
  ui.rerender(<TempoEstimatePanel trial={tempoTrial} sourceJobId="34567890-1234-4123-8123-123456789012" onAdopt={ui.onAdopt} onInvalidate={ui.onInvalidate} />);
  expect(sourceSelect()).toHaveValue(""); expect(screen.queryByRole("button", { name: /BPMを入力候補に選ぶ/ })).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(2);
});
it("hashes uploaded bytes and labels their MIDI correspondence as unverified", async () => {
  const hash = new Uint8Array(32).fill(0xcc).buffer;
  vi.stubGlobal("crypto", { subtle: { digest: vi.fn().mockResolvedValue(hash) } });
  const file = new File(["synthetic"], "synthetic.flac");
  const report = { ...tempoReport(), source: { kind: "uploaded-audio", audio_sha256: "c".repeat(64), audio_bytes: file.size, duration_seconds: 20, sample_rate: 16000, channels: 1, format: "FLAC", correspondence: "unverified-upload" } };
  const fetcher = vi.fn().mockResolvedValue(tempoResponse(report)); vi.stubGlobal("fetch", fetcher); const ui = mount(undefined);
  fireEvent.change(sourceSelect(), { target: { value: "upload" } });
  fireEvent.change(screen.getByLabelText("推定用音声 WAV/FLAC"), { target: { files: [file] } }); fireEvent.click(estimateButton());
  await screen.findByText("BPM候補あり・未反映");
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/tempo-estimates/upload"); expect(crypto.subtle.digest).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/アップロードした音声と、この保存MIDIの対応は未検証/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /^120 BPMを入力候補に選ぶ/ }));
  expect(ui.onAdopt).toHaveBeenCalledWith(expect.objectContaining({ evidence: expect.stringContaining("MIDI対応未検証") }));
});
it("rejects empty/unsupported upload locally and preserves manual-only use after an API failure", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher); mount();
  fireEvent.change(sourceSelect(), { target: { value: "upload" } });
  fireEvent.change(screen.getByLabelText("推定用音声 WAV/FLAC"), { target: { files: [new File([], "empty.wav")] } }); fireEvent.click(estimateButton());
  expect(await screen.findByRole("alert")).toHaveTextContent("0バイトではない"); expect(fetcher).not.toHaveBeenCalled();
  fetcher.mockResolvedValueOnce(tempoResponse({ detail: { code: "source_changed", message: "保存音声の対応を再確認してください。" } }, 409));
  fireEvent.change(sourceSelect(), { target: { value: "stored" } }); fireEvent.click(screen.getByRole("button", { name: "保存音声の対応を確認" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("source_changed"));
});
it("does not upload a replaced file after its old SHA computation completes", async () => {
  let finish: ((hash: ArrayBuffer) => void) | undefined;
  vi.stubGlobal("crypto", { subtle: { digest: vi.fn().mockImplementation(() => new Promise<ArrayBuffer>(resolve => { finish = resolve; })) } });
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher); mount();
  fireEvent.change(sourceSelect(), { target: { value: "upload" } });
  const input = screen.getByLabelText("推定用音声 WAV/FLAC");
  fireEvent.change(input, { target: { files: [new File(["synthetic first"], "first.wav")] } }); fireEvent.click(estimateButton());
  await waitFor(() => expect(crypto.subtle.digest).toHaveBeenCalledTimes(1));
  fireEvent.change(input, { target: { files: [new File(["synthetic replacement"], "replacement.wav")] } });
  await act(async () => { finish?.(new Uint8Array(32).buffer); });
  expect(fetcher).not.toHaveBeenCalled(); expect(screen.queryByText("BPM候補あり・未反映")).toBeNull(); expect(estimateButton()).toBeEnabled();
});
