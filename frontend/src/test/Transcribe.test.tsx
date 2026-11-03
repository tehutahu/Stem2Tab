import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Transcribe from "../pages/Transcribe";
import { workflowJson } from "../utils/workflowHttp";
const id = "12345678-1234-4123-8123-123456789012";
const health = { model_assets: "configured", reasons: [], max_audio_seconds: 600, max_upload_bytes: 536870912 };
const running = { job_id: id, status: "running", stage: "transcribing", cancel_requested: false, completed_chunks: 1, total_chunks: 3, midi_trial_id: null, error: null, available_files: [] };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
const mount = () => render(<MemoryRouter><Transcribe /></MemoryRouter>);
async function fill(): Promise<void> {
  await screen.findByText(/配置済み/);
  fireEvent.change(screen.getByLabelText("音源 WAV/FLAC/M4A/Opus"), { target: { files: [new File(["synthetic"], "test.wav")] } });
  fireEvent.click(screen.getByLabelText(/使用権/));
}
beforeEach(() => { sessionStorage.clear(); vi.spyOn(crypto, "randomUUID").mockReturnValue(id); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); sessionStorage.clear(); });
it("opens an explicit saved-job link with GET only and preserves the prior session", async () => {
  const prior = "23456789-1234-4123-8123-123456789012";
  sessionStorage.setItem("stem2tab.song.request", prior);
  window.history.replaceState(null, "", `/?job=${id}`);
  const fetch = vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(response({ ...running, status: "cancelled", error: null }));
  vi.stubGlobal("fetch", fetch);
  try {
    mount();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(fetch.mock.calls[1]?.[0]).toBe(`/api/v1/transcriptions/${id}`);
    expect(fetch.mock.calls.every((call) => call[1]?.method !== "POST")).toBe(true);
    expect(sessionStorage.getItem("stem2tab.song.request")).toBe(prior);
  } finally { window.history.replaceState(null, "", "/"); }
});
it.each(["m4a", "opus", "ogg"])("accepts local %s without changing original file bytes in the request", async (extension) => {
  const fetch = vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(response(running, 202));
  vi.stubGlobal("fetch", fetch); mount(); await screen.findByText(/配置済み/);
  const input = screen.getByLabelText("音源 WAV/FLAC/M4A/Opus");
  expect(input).toHaveAttribute("accept", ".wav,.flac,.m4a,.opus,.ogg");
  const file = new File(["synthetic"], `sample.${extension}`);
  fireEvent.change(input, { target: { files: [file] } }); fireEvent.click(screen.getByLabelText(/使用権/));
  fireEvent.click(screen.getByRole("button", { name: /分離して採譜/ }));
  await screen.findByText(/採譜区間/);
  const body: unknown = fetch.mock.calls[1]?.[1]?.body;
  if (!(body instanceof FormData)) throw new Error("Missing upload request");
  expect(body.get("file")).toBe(file); expect(fetch).toHaveBeenCalledTimes(2);
});
it.each(["wav", "flac", "m4a", "opus", "ogg"])("blocks a zero-byte %s locally and permits choosing a nonempty input", async (extension) => {
  const fetch = vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(response(running, 202));
  vi.stubGlobal("fetch", fetch); mount(); await screen.findByText(/配置済み/);
  const input = screen.getByLabelText("音源 WAV/FLAC/M4A/Opus");
  fireEvent.change(input, { target: { files: [new File([], `empty.${extension}`)] } });
  fireEvent.click(screen.getByLabelText(/使用権/));
  expect(screen.getByRole("alert")).toHaveTextContent("0バイトの空ファイル");
  const submit = screen.getByRole("button", { name: /分離して採譜/ });
  expect(submit).toBeDisabled(); fireEvent.click(submit);
  expect(fetch).toHaveBeenCalledTimes(1); expect(sessionStorage.getItem("stem2tab.song.request")).toBeNull();
  fireEvent.change(input, { target: { files: [new File(["synthetic"], `valid.${extension}`)] } });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument(); expect(submit).toBeEnabled();
  fireEvent.click(submit); await screen.findByText(/採譜区間/); expect(fetch).toHaveBeenCalledTimes(2);
});
it("keeps unavailable assets explicit with no model fallback", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ ...health, model_assets: "unavailable", reasons: [{ message: "No local weights" }] })));
  mount(); expect(await screen.findByText("No local weights")).toBeVisible(); expect(screen.getByRole("button", { name: /分離して/ })).toBeDisabled();
  expect(screen.getByText(/Basic Pitchへの自動切替/)).toBeVisible();
});
it("presents retained zero-duration output for review without another audio/model request",async()=>{
  sessionStorage.setItem("stem2tab.song.request",id);
  const value={...running,status:"needs_review",stage:"output_needs_review",available_files:["model-events.mid","model-notes.json"],
    review:{kind:"zero_duration",raw_note_count:3,zero_duration_count:1,positive_note_count:2,recorded_status:"needs_review",notes_modified:false}};
  const fetch=vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(response(value));vi.stubGlobal("fetch",fetch);mount();
  await screen.findByText("推論出力を保存済み・譜面化の確認待ち");
  expect(screen.getByRole("link",{name:/全推論イベントの生MIDI/})).toHaveAttribute("href",`/api/v1/transcriptions/${id}/files/model-events.mid`);
  expect(screen.getByRole("button",{name:/ゼロ長1件を除いた別版/})).toBeEnabled();
  expect(screen.queryByRole("button",{name:/新しい試行として再実行/})).toBeNull();
  expect(fetch.mock.calls.every(c=>c[1]?.method!=="POST")).toBe(true);
});
it("submits once with selected bounds, and recovers same ID after reload", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(response(running, 202)); vi.stubGlobal("fetch", fetch);
  const view = mount(); await fill(); fireEvent.change(screen.getByLabelText("処理範囲"), { target: { value: "range" } });
  fireEvent.change(screen.getByLabelText("開始秒"), { target: { value: "2" } });
  const button = screen.getByRole("button", { name: /分離して/ }); fireEvent.click(button); fireEvent.click(button);
  expect(await screen.findByText("採譜区間 1 / 3")).toBeVisible(); expect(fetch).toHaveBeenCalledTimes(2);
  const call = fetch.mock.calls[1]; expect(call?.[1]?.body).toBeInstanceOf(FormData);
  const data: unknown = call?.[1]?.body;
  if (!(data instanceof FormData)) throw new Error("Missing form");
  expect(data.get("start_seconds")).toBe("2"); expect(data.get("end_seconds")).toBe("10"); expect(data.get("method")).toBeNull();
  view.unmount(); fetch.mockImplementation((url: string) => Promise.resolve(response(url.endsWith("/health") ? health : running))); mount(); await screen.findByText(/採譜区間/);
  expect(fetch.mock.calls.filter((c) => c[0] === `/api/v1/transcriptions/${id}`)).toHaveLength(1);
});
it("requests actual cancellation once and waits for terminal confirmation", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(response(running)).mockResolvedValueOnce(response({ ...running, cancel_requested: true })); vi.stubGlobal("fetch", fetch);
  mount(); await fill(); fireEvent.click(screen.getByRole("button", { name: /分離して/ })); await screen.findByText(/採譜区間/);
  fireEvent.click(screen.getByRole("button", { name: "処理を取り消す" })); fireEvent.click(screen.getByRole("button", { name: "処理を取り消す" }));
  expect(await screen.findByText(/取消要求済み/)).toBeVisible(); expect(fetch).toHaveBeenCalledTimes(3);
  expect(screen.getByRole("button", { name: "処理を取り消す" })).toBeDisabled();
});
it("preserves unknown POST identity after an empty 500, then recovers", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(new Response("", { status: 500 })).mockResolvedValueOnce(response(running)); vi.stubGlobal("fetch", fetch);
  mount(); await fill(); fireEvent.click(screen.getByRole("button", { name: /分離して/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("実行結果は未確認"); expect(sessionStorage.getItem("stem2tab.song.request")).toBe(id);
  expect(screen.getByLabelText("音源 WAV/FLAC/M4A/Opus")).toBeDisabled(); fireEvent.click(screen.getByRole("button", { name: /同じ処理IDを再確認/ }));
  await screen.findByText(/採譜区間/); expect(fetch.mock.calls.filter((c) => c[1]?.method === "POST")).toHaveLength(1);
});
it("a known 4xx rejection unlocks input without a phantom pending job", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(response({}, 409)));
  mount(); await fill(); fireEvent.click(screen.getByRole("button", { name: /分離して/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("拒否"); expect(screen.getByLabelText("音源 WAV/FLAC/M4A/Opus")).toBeEnabled();
  expect(sessionStorage.getItem("stem2tab.song.request")).toBeNull();
});
it("shows the 422 reason and preserves the selected file for an explicit retry", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response(health))
    .mockResolvedValueOnce(response({ detail: { code: "invalid_selection", message: "指定区間が音源の範囲外です。" } }, 422))
    .mockResolvedValueOnce(response(running, 202));
  vi.stubGlobal("fetch", fetch); mount(); await fill();
  fireEvent.click(screen.getByRole("button", { name: /分離して/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("指定区間が音源の範囲外です。 [invalid_selection]");
  expect(screen.getByRole("alert")).not.toHaveTextContent("起動状態");
  expect(sessionStorage.getItem("stem2tab.song.request")).toBeNull();
  expect(screen.getByLabelText("音源 WAV/FLAC/M4A/Opus")).toBeEnabled();
  const first: unknown = fetch.mock.calls[1]?.[1]?.body;
  fireEvent.click(screen.getByRole("button", { name: /分離して/ }));
  await screen.findByText(/採譜区間/);
  const second: unknown = fetch.mock.calls[2]?.[1]?.body;
  if (!(first instanceof FormData) || !(second instanceof FormData)) throw new Error("Missing forms");
  expect(second.get("file")).toBe(first.get("file"));
  expect(fetch).toHaveBeenCalledTimes(3);
});
it("restores an existing result when a new attempt is rejected", async () => {
  sessionStorage.setItem("stem2tab.song.request", id);
  const next = "23456789-1234-4123-8123-123456789012";
  vi.spyOn(crypto, "randomUUID").mockReturnValue(next);
  const cancelled = { ...running, status: "cancelled", error: { message: "Stopped" }, available_files: ["original.audio"] };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(response(cancelled))
    .mockResolvedValueOnce(response({ detail: { code: "invalid_checkpoint", message: "Saved checkpoint is unreadable." } }, 409)));
  mount(); await screen.findByText("Stopped");
  fireEvent.click(screen.getByRole("button", { name: /新しい試行として再実行/ }));
  await screen.findByRole("alert");
  expect(screen.getByText(`処理ID: ${id}`)).toBeVisible();
  expect(screen.getByRole("link", { name: "元音源を保存" })).toHaveAttribute("href", `/api/v1/transcriptions/${id}/files/original.audio`);
  expect(sessionStorage.getItem("stem2tab.song.request")).toBe(id);
});
it("ignores an old polling response after cancellation", async () => {
  sessionStorage.setItem("stem2tab.song.request", id);
  let finish: ((response: Response) => void) | undefined;
  const fetch = vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(response(running)).mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; })).mockResolvedValueOnce(response({ ...running, status: "cancelled", error: { message: "Stopped" } })); vi.stubGlobal("fetch", fetch);
  mount(); await screen.findByText(/採譜区間/); vi.useFakeTimers();
  // The existing poll timer was created before fake timers; use a reload query instead.
  vi.useRealTimers(); await waitFor(() => expect(finish).toBeDefined(), { timeout: 2000 });
  fireEvent.click(screen.getByRole("button", { name: "処理を取り消す" })); await screen.findByText("Stopped");
  await act(async () => { finish?.(response({ ...running, status: "completed", midi_trial_id: id })); });
  expect(screen.queryByRole("link", { name: /3. 試聴/ })).not.toBeInTheDocument();
});
it.each([
  new Response("", { status: 200, headers: { "content-type": "application/json" } }),
  new Response("<html/>", { headers: { "content-type": "text/html" } }),
  new Response("{", { headers: { "content-type": "application/json" } }),
])("rejects invalid response bodies before JSON interpretation", async (result) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(result)); await expect(workflowJson("/api/test")).rejects.toThrow();
});
it("late health responses cannot overwrite a newer result", async () => {
  let resolve: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => new Promise<Response>((r) => { resolve = r; })).mockResolvedValueOnce(response(health)));
  mount(); fireEvent.click(screen.getByRole("button", { name: "接続を再確認" })); await screen.findByText(/配置済み（GPU/);
  await act(async () => { resolve?.(response({ ...health, model_assets: "unavailable" })); });
  expect(screen.getByText(/配置済み（GPU/)).toBeVisible();
});
it("does not POST without a secure request identifier", async () => {
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(response(health))); vi.stubGlobal("fetch", fetch);
  vi.spyOn(crypto, "randomUUID").mockImplementation(() => { throw new Error("Unavailable"); });
  mount(); await fill(); fireEvent.click(screen.getByRole("button", { name: /分離して/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("安全な処理ID"); expect(fetch).toHaveBeenCalledTimes(1);
});

it("identifies unsaved input and advances the accessible step and progress after submission", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(response(running)));
  mount(); await fill();
  expect(screen.getByText(/未送信・未保存/)).toBeVisible();
  const steps = screen.getByRole("list", { name: "採譜の流れ" });
  expect(steps.querySelector('[aria-current="step"]')).toHaveTextContent("音源と範囲を選ぶ");
  fireEvent.click(screen.getByRole("button", { name: /分離して採譜/ }));
  expect(await screen.findByRole("progressbar", { name: "採譜区間の進捗" })).toHaveAttribute("value", "1");
  expect(screen.getByRole("region", { name: "処理状況" })).toHaveFocus();
  expect(steps.querySelector('[aria-current="step"]')).toHaveTextContent("分離して採譜");
});

it("keeps a completed result and precision warning visible while grouping diagnostic downloads", async () => {
  sessionStorage.setItem("stem2tab.song.request", id);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(health)).mockResolvedValueOnce(response({ ...running, status: "completed", stage: "completed", midi_trial_id: id, available_files: ["performance.mid", "model-notes.json"] })));
  mount();
  expect(await screen.findByRole("heading", { name: "採譜結果を保存しました" })).toBeVisible();
  expect(screen.getByRole("link", { name: "3. 試聴・五線譜・TAB・保存へ" })).toHaveAttribute("href", `/midi-trial/saved/${id}`);
  expect(screen.getByRole("link", { name: "原MIDIを保存" })).toBeVisible();
  const disclosure = screen.getByText("音符JSON・推論記録を保存").closest("details");
  expect(disclosure).not.toHaveAttribute("open");
  fireEvent.click(screen.getByText("音符JSON・推論記録を保存"));
  expect(disclosure).toHaveAttribute("open");
  expect(screen.getByRole("link", { name: "元の全音符JSONを保存" })).toHaveAttribute("href", `/api/v1/transcriptions/${id}/files/model-notes.json`);
  expect(screen.getByText(/長曲の実モデル動作・採譜精度は未検証/)).toBeVisible();
  expect(screen.getByRole("list", { name: "採譜の流れ" }).querySelector('[aria-current="step"]')).toHaveTextContent("確認・試聴・保存");
});
