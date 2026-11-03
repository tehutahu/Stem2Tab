import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import OutputReview, { parseOutputReview } from "../components/OutputReview";
const job="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", id="12345678-1234-4123-8123-123456789012";
const key=`stem2tab.output-review.${job}`;
const info={raw_note_count:3,zero_duration_count:1,positive_note_count:2,recorded_status:"failed"} satisfies Parameters<typeof OutputReview>[0]["info"];
const completed={request_id:id,source_job_id:job,status:"completed",policy:"exclude_zero_duration",raw_note_count:3,excluded_note_count:1,retained_note_count:2,midi_trial_id:id};
const response=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status,headers:{"content-type":"application/json"}});
const mount=()=>render(<MemoryRouter><OutputReview jobId={job} info={info}/></MemoryRouter>);
beforeEach(()=>{sessionStorage.clear();vi.spyOn(crypto,"randomUUID").mockReturnValue(id);});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();sessionStorage.clear();});
it("keeps all source rows visible and does nothing until an explicit choice",async()=>{
  const fetch=vi.fn().mockResolvedValue(response(completed));vi.stubGlobal("fetch",fetch);mount();
  expect(screen.getByText(/全3音のうち/)).toHaveTextContent("元の全音符");
  expect(screen.getByText(/以前のfailed記録/)).toBeVisible();expect(fetch).not.toHaveBeenCalled();
  const button=screen.getByRole("button",{name:/別版を作成/});fireEvent.click(button);fireEvent.click(button);
  await screen.findByRole("link",{name:"2音の別版を試聴・譜面で確認"});expect(fetch).toHaveBeenCalledTimes(1);
  const form:unknown=fetch.mock.calls[0]?.[1]?.body;
  if(!(form instanceof FormData))throw new Error("Missing explicit review form");
  expect(form.get("policy")).toBe("exclude_zero_duration");expect(form.get("request_id")).toBe(id);
  expect(screen.getByRole("link")).toHaveAttribute("href",`/midi-trial/saved/${id}?view=staff&spacing=time`);
});
it("recovers an uncertain creation with GET of the same ID and no second POST",async()=>{
  const fetch=vi.fn().mockResolvedValueOnce(new Response("",{status:500})).mockResolvedValueOnce(response(completed));vi.stubGlobal("fetch",fetch);mount();
  fireEvent.click(screen.getByRole("button",{name:/別版を作成/}));await screen.findByRole("alert");
  expect(sessionStorage.getItem(key)).toBe(id);expect(screen.getByRole("button",{name:/別版を作成/})).toBeDisabled();
  fireEvent.click(screen.getByRole("button",{name:"同じ候補IDを確認"}));await screen.findByRole("link");
  expect(fetch.mock.calls.map(c=>c[1]?.method)).toEqual(["POST","GET"]);
});
it("reloads a pending preview by GET only",async()=>{
  sessionStorage.setItem(key,id);const fetch=vi.fn().mockResolvedValue(response(completed));vi.stubGlobal("fetch",fetch);mount();
  await screen.findByRole("link");expect(fetch).toHaveBeenCalledTimes(1);expect(fetch.mock.calls[0]?.[1]?.method).toBe("GET");
});
it("shows validation details without losing the original audio-job identifier",async()=>{
  sessionStorage.setItem("stem2tab.song.request",job);
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue(response({detail:{code:"review_preview_limit",message:"Preview is too large."}},422)));mount();
  fireEvent.click(screen.getByRole("button",{name:/別版を作成/}));expect(await screen.findByRole("alert")).toHaveTextContent("Preview is too large");
  expect(sessionStorage.getItem("stem2tab.song.request")).toBe(job);expect(sessionStorage.getItem(key)).toBeNull();
});
it("does not link a different source or silently accept different counts",async()=>{
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue(response({...completed,retained_note_count:3})));mount();
  fireEvent.click(screen.getByRole("button",{name:/別版を作成/}));await screen.findByRole("alert");
  expect(screen.queryByRole("link")).toBeNull();expect(sessionStorage.getItem(key)).toBe(id);
});
it("validates the preserved-output count contract",()=>{
  expect(parseOutputReview({...info,kind:"zero_duration",notes_modified:false})).toEqual(info);
  expect(()=>parseOutputReview({...info,kind:"zero_duration",notes_modified:true})).toThrow();
  expect(()=>parseOutputReview({...info,kind:"zero_duration",notes_modified:false,positive_note_count:3})).toThrow();
});
