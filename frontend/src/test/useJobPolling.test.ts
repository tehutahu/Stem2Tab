import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useJobPolling } from "../hooks/useJobPolling";
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
it.each(["SUCCESS", "FAILURE", "REVOKED"])("stops polling at terminal %s", async status => {
  vi.useFakeTimers(); const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job_id: "a", status }) }); vi.stubGlobal("fetch", fetcher);
  const view = renderHook(() => useJobPolling("a", 50)); await act(async () => {});
  expect(view.result.current.data?.status).toBe(status);
  await act(async () => { vi.advanceTimersByTime(500); }); expect(fetcher).toHaveBeenCalledOnce();
});
it("does not overlap polls or show an old job after the input changes", async () => {
  vi.useFakeTimers(); let complete!: (v: unknown) => void;
  const fetcher = vi.fn().mockImplementationOnce(() => new Promise(resolve => { complete = resolve; })).mockResolvedValue({ ok: true, json: async () => ({ job_id: "b", status: "SUCCESS" }) }); vi.stubGlobal("fetch", fetcher);
  const view = renderHook(({ id }) => useJobPolling(id, 50), { initialProps: { id: "a" } });
  await act(async () => { vi.advanceTimersByTime(500); }); expect(fetcher).toHaveBeenCalledOnce();
  view.rerender({ id: "b" }); await act(async () => {}); expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  await act(async () => complete({ ok: true, json: async () => ({ job_id: "a", status: "SUCCESS", files: ["old.gp5"] }) }));
  expect(view.result.current.data?.job_id).toBe("b"); expect(view.result.current.data?.files).toBeUndefined();
});
