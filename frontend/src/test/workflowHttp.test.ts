import { afterEach, expect, it, vi } from "vitest";
import { WorkflowHttpError, workflowJson } from "../utils/workflowHttp";

afterEach(() => { vi.unstubAllGlobals(); });
const json = (value: unknown, status: number) => new Response(JSON.stringify(value), {
  status, headers: { "content-type": "application/json; charset=utf-8" },
});

it("preserves the actual 422 reason and code, without API-start advice", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: {
    code: "duration_limit", message: "処理区間は600秒以内を明示してください。",
  } }, 422)));
  const error: unknown = await workflowJson("/api/test").catch(cause => cause);
  expect(error).toBeInstanceOf(WorkflowHttpError);
  if (!(error instanceof WorkflowHttpError)) throw new Error("HTTP error required");
  expect(error.status).toBe(422);
  expect(error.message).toContain("600秒");
  expect(error.message).toContain("duration_limit");
  expect(error.message).not.toContain("起動状態");
});

it("shows FastAPI validation fields without echoing input values or context", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: [
    { loc: ["body", "file"], msg: "Field required", input: "private filename" },
    { loc: ["body", "end_seconds"], msg: "Input should be a valid number", ctx: { error: "private context" } },
  ] }, 422)));
  await expect(workflowJson("/api/test")).rejects.toThrow("音源: Field required / 終了秒: Input should be a valid number");
});

it.each([
  new Response("", { status: 422 }),
  new Response("<html>private proxy page</html>", { status: 422, headers: { "content-type": "text/html" } }),
  new Response("{", { status: 422, headers: { "content-type": "application/json" } }),
  json({ detail: { input: "private input" } }, 422),
])("retains HTTP rejection when its detail is unavailable", async (response) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  await expect(workflowJson("/api/test")).rejects.toThrow("入力の検証で拒否されました");
});

it("keeps 503 as an HTTP error with its public service reason", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: { code: "api_unavailable", message: "Restore local API." } }, 503)));
  await expect(workflowJson("/api/test")).rejects.toThrow("API応答 HTTP 503。Restore local API. [api_unavailable]");
});

it("keeps a received HTTP rejection even if reading its body fails", async () => {
  const response = json({}, 422);
  vi.spyOn(response, "text").mockRejectedValue(new TypeError("Disconnected body"));
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  await expect(workflowJson("/api/test")).rejects.toBeInstanceOf(WorkflowHttpError);
});
