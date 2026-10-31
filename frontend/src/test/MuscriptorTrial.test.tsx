import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import MuscriptorTrial from "../pages/MuscriptorTrial";
const id = "12345678-1234-4123-8123-123456789012";
const midiId = "12345678-1234-4123-8123-123456789013";
const available = { available: true, reasons: [], max_audio_seconds: 10, max_upload_bytes: 10485760 };
const running = { job_id: id, status: "running", stage: "queued", midi_trial_id: null, error: null };
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body });
const mount = () => render(<MemoryRouter><MuscriptorTrial /></MemoryRouter>);
function fill(): void {
  fireEvent.change(screen.getByLabelText("実験方式"), { target: { value: "muscriptor-small" } });
  fireEvent.change(screen.getByLabelText("音源 WAV/FLAC"), { target: { files: [new File(["fake audio"], "bass.wav")] } });
  fireEvent.click(screen.getByLabelText(/使用権のある音源/));
  fireEvent.change(screen.getByLabelText("弦構成"), { target: { value: "bass4" } });
}
beforeEach(() => { sessionStorage.clear(); vi.spyOn(crypto, "randomUUID").mockReturnValue(id); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); sessionStorage.clear(); });
it("shows unavailable deployment reasons and disables execution", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ ...available, available: false, reasons: [{ code: "missing_weights", message: "資材なし" }] })));
  mount(); expect(await screen.findByText(/missing_weights: 資材なし/)).toBeVisible(); expect(screen.getByRole("button", { name: /ローカル実験を実行/ })).toBeDisabled();
});
it.each([null, { available: true }, { ...available, reasons: [null] }])("rejects malformed availability %j", async (body) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(body))); mount(); expect(await screen.findByRole("alert")).toHaveTextContent("応答形式"); expect(screen.getByRole("button", { name: /ローカル実験を実行/ })).toBeDisabled();
});
it("requires explicit method, rights and profile, with GP5 off", async () => {
  const fetchMock = vi.fn().mockResolvedValue(response(available)); vi.stubGlobal("fetch", fetchMock); mount();
  await screen.findByText("実験経路を利用できます"); expect(screen.getByLabelText("実験方式")).toHaveValue(""); expect(screen.getByLabelText(/GP5運指プレビュー/)).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: /ローカル実験を実行/ })); expect(screen.getByRole("alert")).toHaveTextContent("使用権"); expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("submits explicit settings once, locks inputs and polls to a saved-MIDI link", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(response(available)).mockResolvedValueOnce(response(running, 202)).mockResolvedValueOnce(response({ ...running, status: "completed", stage: "done", midi_trial_id: midiId }));
  vi.stubGlobal("fetch", fetchMock); mount(); await screen.findByText("実験経路を利用できます"); fill(); vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: /ローカル実験を実行/ }));
  await act(async () => {}); expect(screen.getByLabelText("実験方式")).toBeDisabled();
  const body = fetchMock.mock.calls[1][1].body as FormData; expect(body.get("request_id")).toBe(id); expect(body.get("method")).toBe("muscriptor-small"); expect(body.get("rights_confirmed")).toBe("true"); expect(body.get("gp5_preview")).toBe("false");
  fireEvent.submit(screen.getByRole("button", { name: /実験処理中/ }).closest("form")!); expect(fetchMock).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByRole("link", { name: /保存MIDI/ })).toHaveAttribute("href", `/midi-trial/saved/${midiId}`); expect(screen.getByLabelText("実験方式")).toBeEnabled(); vi.useRealTimers();
});
it("validates audio type, size and custom settings before POST", async () => {
  const fetchMock = vi.fn().mockResolvedValue(response(available)); vi.stubGlobal("fetch", fetchMock); mount(); await screen.findByText("実験経路を利用できます"); fill();
  fireEvent.change(screen.getByLabelText("音源 WAV/FLAC"), { target: { files: [new File(["fake"], "bass.mp3")] } }); fireEvent.click(screen.getByRole("button", { name: /ローカル実験を実行/ })); expect(screen.getByRole("alert")).toHaveTextContent("WAVまたはFLAC");
  fireEvent.change(screen.getByLabelText("音源 WAV/FLAC"), { target: { files: [new File([new Uint8Array(10485761)], "bass.flac")] } }); fireEvent.click(screen.getByRole("button", { name: /ローカル実験を実行/ })); expect(screen.getByRole("alert")).toHaveTextContent("10 MiB");
  fireEvent.change(screen.getByLabelText("音源 WAV/FLAC"), { target: { files: [new File(["fake"], "bass.flac")] } }); fireEvent.change(screen.getByLabelText("弦構成"), { target: { value: "custom" } }); fireEvent.change(screen.getByLabelText(/開放弦MIDI/), { target: { value: "28,28" } }); fireEvent.click(screen.getByRole("button", { name: /ローカル実験を実行/ })); expect(screen.getByRole("alert")).toHaveTextContent("昇順"); expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("preserves a running job on polling failure and resumes the same job", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(response(available)).mockResolvedValueOnce(response(running, 202)).mockResolvedValueOnce(response({ ...running, job_id: "invalid" })).mockResolvedValueOnce(response({ ...running, status: "failed", error: { code: "bass_stem_missing", message: "ベースstemがありません" } }));
  vi.stubGlobal("fetch", fetchMock); mount(); await screen.findByText("実験経路を利用できます"); fill(); vi.useFakeTimers(); fireEvent.click(screen.getByRole("button", { name: /ローカル実験を実行/ })); await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); expect(screen.getByRole("alert")).toHaveTextContent("状態確認エラー"); expect(screen.getByLabelText("実験方式")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: /同じジョブ/ })); await act(async () => {}); expect(screen.getByRole("alert")).toHaveTextContent("bass_stem_missing"); expect(fetchMock.mock.calls[3][0]).toBe(`/api/v1/muscriptor-trials/${id}`);
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); }); expect(fetchMock).toHaveBeenCalledTimes(4); vi.useRealTimers();
});
it("aborts POST on unmount and ignores a late completion", async () => {
  let resolve!: (value: unknown) => void;
  const fetchMock = vi.fn().mockResolvedValueOnce(response(available)).mockImplementationOnce(() => new Promise((r) => { resolve = r; })); vi.stubGlobal("fetch", fetchMock);
  const view = mount(); await screen.findByText("実験経路を利用できます"); fill(); fireEvent.click(screen.getByRole("button", { name: /ローカル実験を実行/ }));
  const signal = fetchMock.mock.calls[1][1].signal; view.unmount(); expect(signal.aborted).toBe(true); await act(async () => resolve(response(running, 202))); expect(fetchMock).toHaveBeenCalledTimes(2);
});
it("allows another explicit request after a confirmed HTTP validation rejection", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(response(available)).mockResolvedValue(response({ detail: { code: "audio_too_long", message: "10秒を超えています" } }, 422));
  vi.stubGlobal("fetch", fetchMock); mount(); await screen.findByText("実験経路を利用できます"); fill(); fireEvent.click(screen.getByRole("button", { name: /ローカル実験を実行/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("audio_too_long"); expect(screen.getByRole("button", { name: /ローカル実験を実行/ })).toBeEnabled(); expect(sessionStorage.length).toBe(0);
});
it.each(["network", "json", "malformed", "server"])("locks an uncertain POST %s and recovers only by same-ID GET", async (failure) => {
  const fetchMock = vi.fn().mockResolvedValueOnce(response(available)).mockImplementationOnce(async () => {
    if (failure === "network") throw new Error("connection lost");
    if (failure === "json") return { status: 202, json: async () => { throw new Error("bad JSON"); } };
    return response(failure === "malformed" ? { job_id: "bad" } : { detail: { code: "server_error", message: "unknown" } }, failure === "server" ? 500 : 202);
  }).mockResolvedValueOnce(response({ detail: { code: "not_found", message: "not found" } }, 404)).mockResolvedValueOnce(response({ ...running, status: "completed", midi_trial_id: midiId }));
  vi.stubGlobal("fetch", fetchMock); mount(); await screen.findByText("実験経路を利用できます"); fill(); fireEvent.click(screen.getByRole("button", { name: /ローカル実験を実行/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("実行結果が不明"); expect(screen.getByLabelText("実験方式")).toBeDisabled(); expect(sessionStorage.getItem("stem2tab.muscriptor.pending-request")).toBe(id);
  fireEvent.submit(screen.getByRole("button", { name: /実験処理中/ }).closest("form")!); expect(fetchMock).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "同じ試行IDの状態を確認" })); await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("not_found")); expect(screen.getByLabelText("実験方式")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "同じ試行IDの状態を確認" })); expect(await screen.findByRole("link", { name: /保存MIDI/ })).toBeVisible();
  expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1); expect(fetchMock.mock.calls[3][0]).toBe(`/api/v1/muscriptor-trials/${id}`); expect(sessionStorage.length).toBe(0);
});
it("restores pending-ID recovery after unmount without a new POST", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(response(available)).mockRejectedValueOnce(new Error("lost reply")).mockResolvedValueOnce(response(available)).mockResolvedValueOnce(response({ ...running, status: "failed", error: { code: "failed", message: "saved failure" } }));
  vi.stubGlobal("fetch", fetchMock); const view = mount(); await screen.findByText("実験経路を利用できます"); fill(); fireEvent.click(screen.getByRole("button", { name: /ローカル実験を実行/ }));
  await screen.findByRole("alert"); view.unmount(); mount(); expect(await screen.findByRole("alert")).toHaveTextContent("saved failure"); expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1); expect(fetchMock.mock.calls[3][0]).toBe(`/api/v1/muscriptor-trials/${id}`);
});
