import { afterEach, describe, expect, it, vi } from "vitest";
import { readReviewJson, ReviewResponseError } from "../utils/reviewFetch";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const quiet = (): void => { vi.spyOn(console, "error").mockImplementation(() => {}); };

describe("local review JSON boundary", () => {
  it("accepts an actual successful metadata response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"tracks":["baseline"]}', { headers: { "content-type": "application/json" } })));
    expect(await readReviewJson("/metadata.json", "比較情報")).toEqual({ tracks: ["baseline"] });
  });
  it.each([
    [500, "text/plain", "", "http", 0],
    [200, "application/json", "", "empty", 0],
    [200, "text/html", "<!doctype html>", "type", 15],
    [200, "application/json", '{"tracks":', "parse", 10],
    [409, "application/json", '{"detail":"changed"}', "http", 20],
  ] as const)("rejects status%s/%s without inventing success", async (status, type, body, stage, length) => {
    quiet();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status, headers: { "content-type": type } })));
    const failure = await readReviewJson("/api/v1/listening-trials/test", "比較音源情報").catch(error => error as unknown);
    expect(failure).toBeInstanceOf(ReviewResponseError);
    if (!(failure instanceof ReviewResponseError)) throw new Error("Expected diagnostic failure");
    expect(failure.diagnostic).toMatchObject({ status, stage, bodyBytes: length, contentType: type });
    expect(failure.message).not.toContain("SyntaxError");
  });
  it("reports a disconnected response stream without claiming a zero-byte body", async () => {
    quiet();const response = new Response("{}", { headers: { "content-type": "application/json" } });
    vi.spyOn(response, "text").mockRejectedValue(new TypeError("terminated"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    await expect(readReviewJson("/metadata.json", "比較情報")).rejects.toMatchObject({ diagnostic: { stage: "read", status: 200, bodyBytes: null } });
  });
  it("reports a connection failure explicitly", async () => {
    quiet();vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(readReviewJson("/metadata.json", "比較情報")).rejects.toMatchObject({ diagnostic: { stage: "fetch", status: null, bodyBytes: null } });
  });
  it("propagates cancellation during fetch without logging it as a server failure", async () => {
    const log=vi.spyOn(console,"error").mockImplementation(()=>{}), controller=new AbortController();
    const aborted=new DOMException("Cancelled", "AbortError");
    vi.stubGlobal("fetch",vi.fn(async()=>{controller.abort(aborted);throw aborted;}));
    await expect(readReviewJson("/metadata.json","比較情報",controller.signal)).rejects.toBe(aborted);
    expect(log).not.toHaveBeenCalled();
  });
  it("discards an old body completion after its signal was cancelled", async () => {
    const log=vi.spyOn(console,"error").mockImplementation(()=>{}), controller=new AbortController();
    let finish!: (body: string)=>void;
    const response=new Response("{}",{headers:{"content-type":"application/json"}});
    vi.spyOn(response,"text").mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
    vi.stubGlobal("fetch",vi.fn(async()=>response));
    const pending=readReviewJson("/metadata.json","比較情報",controller.signal);
    await vi.waitFor(()=>expect(response.text).toHaveBeenCalled());
    controller.abort(); finish('{"old":true}');
    await expect(pending).rejects.toMatchObject({name:"AbortError"});
    expect(log).not.toHaveBeenCalled();
  });
});
