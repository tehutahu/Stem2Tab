import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
const id = "12345678-1234-4123-8123-123456789012";
vi.mock("../hooks/useJobPolling", () => ({ useJobPolling: (jobId?: string) => ({ isLoading: false, data: jobId ? { job_id: jobId, status: "SUCCESS", files: ["bass.mid"] } : undefined }) }));
import Upload from "../pages/Upload";
afterEach(() => { vi.unstubAllGlobals(); });
function mount() { render(<MemoryRouter><Upload /></MemoryRouter>); fireEvent.change(screen.getByLabelText("Upload audio"), { target: { files: [new File(["synthetic"], "test.wav")] } }); }
it("offers the generated MIDI workflow after a successful normal job", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job_id: id }) }); vi.stubGlobal("fetch", fetcher);
  mount(); fireEvent.click(screen.getByRole("button", { name: "Create job" }));
  expect(await screen.findByRole("link", { name: /生成MIDIから/ })).toHaveAttribute("href", `/midi-trial/from-job/${id}?name=bass.mid`);
  expect(fetcher).toHaveBeenCalledOnce();
});
it("does not attach a late upload result after interrupted waiting or resubmit an unknown request", async () => {
  let complete!: (value: unknown) => void;
  const fetcher = vi.fn().mockImplementation(() => new Promise(resolve => { complete = resolve; })); vi.stubGlobal("fetch", fetcher);
  mount(); fireEvent.click(screen.getByRole("button", { name: "Create job" }));
  fireEvent.click(screen.getByRole("button", { name: /送信の待機を中断/ }));
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  await act(async () => complete({ ok: true, json: async () => ({ job_id: id }) }));
  expect(screen.queryByRole("link", { name: /生成MIDIから/ })).toBeNull(); expect(screen.getByRole("button", { name: "Create job" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("保存ジョブID"), { target: { value: id } }); fireEvent.click(screen.getByRole("button", { name: "保存ジョブを確認" }));
  expect(screen.getByRole("link", { name: /生成MIDIから/ })).toBeVisible(); expect(fetcher).toHaveBeenCalledOnce();
});
it("allows an explicit retry after a definite pre-execution upload rejection", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: false, status: 415, text: async () => "unsupported input" }); vi.stubGlobal("fetch", fetcher);
  mount(); fireEvent.click(screen.getByRole("button", { name: "Create job" }));
  await screen.findByText(/unsupported input/); expect(screen.getByRole("button", { name: "Create job" })).toBeEnabled();
});
