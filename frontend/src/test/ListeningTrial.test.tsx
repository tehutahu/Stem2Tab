import { beforeEach, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import ListeningTrial, { isListeningSummary } from "../pages/ListeningTrial";
const id="00000000-0000-4000-8000-000000000001";
const summary={trial_id:id,label:"fixture",duration_seconds:10,source_job_id:id,midi_trial_id:id,note_count:2,
  tracks:["original","bass","synth"].map(key=>({key,url:`/api/v1/listening-trials/${id}/files/${key}`,sha256:"0".repeat(64),bytes:100}))};
beforeEach(()=>{vi.restoreAllMocks();});
it("rejects unsafe or incomplete listening metadata",()=>{
  expect(isListeningSummary(summary,id)).toBe(true);
  expect(isListeningSummary({...summary,tracks:[summary.tracks[0],summary.tracks[0],summary.tracks[2]]},id)).toBe(false);
  expect(isListeningSummary({...summary,tracks:summary.tracks.map(t=>({...t,url:"https://external.invalid/private"}))},id)).toBe(false);
  expect(isListeningSummary({...summary,duration_seconds:NaN},id)).toBe(false);
});
it("shows load error without enabling playback or creating inference",async()=>{
  vi.stubGlobal("fetch",vi.fn(async()=>({ok:false,json:async()=>({})})));
  render(<ListeningTrial trialId={id}/>);expect(await screen.findByRole("alert")).toHaveTextContent("読み込めません");
  expect(screen.getByRole("button",{name:"比較を再生"})).toBeDisabled();expect(fetch).toHaveBeenCalledTimes(1);
});
it("ignores an older metadata completion after input changes",async()=>{
  let finish!:(v:unknown)=>void;const fetcher=vi.fn().mockImplementationOnce(()=>new Promise(r=>{finish=r})).mockResolvedValue({ok:false,json:async()=>({})});
  vi.stubGlobal("fetch",fetcher);const view=render(<ListeningTrial trialId={id}/>);
  view.rerender(<ListeningTrial trialId="00000000-0000-4000-8000-000000000002"/>);
  await screen.findByRole("alert");await act(async()=>finish({ok:true,json:async()=>summary}));
  expect(screen.queryByText("fixture")).toBeNull();expect(fetcher).toHaveBeenCalledTimes(2);
});
