import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import ListeningTrial, { isListeningSummary } from "../pages/ListeningTrial";
const id="00000000-0000-4000-8000-000000000001";
const summary={trial_id:id,label:"fixture",duration_seconds:10,source_job_id:id,midi_trial_id:id,note_count:2,
  tracks:["original","bass","synth"].map(key=>({key,url:`/api/v1/listening-trials/${id}/files/${key}`,sha256:"0".repeat(64),bytes:100}))};
const metadata=(value: unknown, status=200): Response => new Response(JSON.stringify(value),{status,headers:{"content-type":"application/json"}});
beforeEach(()=>{vi.restoreAllMocks();});
afterEach(()=>{vi.unstubAllGlobals();});
it("rejects unsafe or incomplete listening metadata",()=>{
  expect(isListeningSummary(summary,id)).toBe(true);
  expect(isListeningSummary({...summary,tracks:[summary.tracks[0],summary.tracks[0],summary.tracks[2]]},id)).toBe(false);
  expect(isListeningSummary({...summary,tracks:summary.tracks.map(t=>({...t,url:"https://external.invalid/private"}))},id)).toBe(false);
  expect(isListeningSummary({...summary,duration_seconds:NaN},id)).toBe(false);
  expect(isListeningSummary({...summary,tracks:[null, null, null]},id)).toBe(false);
  expect(isListeningSummary(null,id)).toBe(false);
});
it("accepts an optional identified decay track and keeps legacy packets valid",()=>{
  const decay={...summary,decay_preview:{policy:"amplitude-half-life-v1",half_life_seconds:.35},
    tracks:[...summary.tracks,{key:"synth-decay",url:`/api/v1/listening-trials/${id}/files/synth-decay`,sha256:"0".repeat(64),bytes:100}]};
  expect(isListeningSummary(decay,id)).toBe(true);
  expect(isListeningSummary({...summary,decay_preview:null},id)).toBe(true);
  expect(isListeningSummary({...decay,decay_preview:null},id)).toBe(false);
  expect(isListeningSummary({...decay,decay_preview:{policy:"auto-shorten",half_life_seconds:.35}},id)).toBe(false);
  expect(isListeningSummary({...decay,decay_preview:{policy:"amplitude-half-life-v1",half_life_seconds:-1}},id)).toBe(false);
  expect(isListeningSummary({...decay,tracks:summary.tracks},id)).toBe(false);
  expect(isListeningSummary({...decay,tracks:[...decay.tracks,decay.tracks[3]]},id)).toBe(false);
});
it("shows load error without enabling playback or creating inference",async()=>{
  vi.spyOn(console,"error").mockImplementation(()=>{});
  vi.stubGlobal("fetch",vi.fn(async()=>new Response("",{status:500,headers:{"content-type":"text/plain"}})));
  render(<ListeningTrial trialId={id}/>);expect(await screen.findByRole("alert")).toHaveTextContent("読み込めません");
  expect(screen.getByRole("button",{name:"比較を再生"})).toBeDisabled();expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("alert")).toHaveTextContent("HTTP 500");
  expect(screen.getByRole("alert")).not.toHaveTextContent("SyntaxError");
});
it("ignores an older metadata completion after input changes",async()=>{
  vi.spyOn(console,"error").mockImplementation(()=>{});
  let finish!:(v:unknown)=>void;const fetcher=vi.fn().mockImplementationOnce(()=>new Promise(r=>{finish=r})).mockResolvedValue(metadata({},500));
  vi.stubGlobal("fetch",fetcher);const view=render(<ListeningTrial trialId={id}/>);
  view.rerender(<ListeningTrial trialId="00000000-0000-4000-8000-000000000002"/>);
  await screen.findByRole("alert");await act(async()=>finish(metadata(summary)));
  expect(screen.queryByText("fixture")).toBeNull();expect(fetcher).toHaveBeenCalledTimes(2);
});

it("does not install an older decay buffer when its decoding finishes after input changes",async()=>{
  vi.spyOn(console,"error").mockImplementation(()=>{});
  const decay={...summary,decay_preview:{policy:"amplitude-half-life-v1",half_life_seconds:.35},
    tracks:[...summary.tracks,{key:"synth-decay",url:`/api/v1/listening-trials/${id}/files/synth-decay`,sha256:"0".repeat(64),bytes:100}]};
  const close=vi.fn(async()=>{}), create=vi.fn(); let finish!: (value: { duration: number })=>void;
  const decode=vi.fn().mockResolvedValue({duration:10}).mockImplementationOnce(async()=>({duration:10}))
    .mockImplementationOnce(async()=>({duration:10})).mockImplementationOnce(async()=>({duration:10}))
    .mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}));
  vi.stubGlobal("AudioContext",class { decodeAudioData=decode; close=close; createBufferSource=create; });
  vi.stubGlobal("crypto",{subtle:{digest:vi.fn(async()=>new ArrayBuffer(32))}});
  const fetcher=vi.fn(async(input: string)=>input.endsWith(id)
    ? metadata(decay)
    : input.includes("/files/") ? {ok:true,arrayBuffer:async()=>new ArrayBuffer(100)}
    : metadata({},500));
  vi.stubGlobal("fetch",fetcher);
  const view=render(<ListeningTrial trialId={id}/>);
  await vi.waitFor(()=>expect(decode).toHaveBeenCalledTimes(4));
  view.rerender(<ListeningTrial trialId="00000000-0000-4000-8000-000000000002"/>);
  await screen.findByRole("alert"); await act(async()=>finish({duration:10}));
  expect(screen.queryByText("fixture")).toBeNull();
  expect(screen.getByRole("button",{name:"比較を再生"})).toBeDisabled();
  expect(screen.queryByRole("button",{name:"採譜再生（減衰あり・試聴のみ）"})).toBeNull();
  expect(close).toHaveBeenCalledTimes(1); expect(create).not.toHaveBeenCalled();
});

it("reports unavailable decay audio explicitly without silently enabling legacy fallback",async()=>{
  const decay={...summary,decay_preview:{policy:"amplitude-half-life-v1",half_life_seconds:.35},
    tracks:[...summary.tracks,{key:"synth-decay",url:`/api/v1/listening-trials/${id}/files/synth-decay`,sha256:"0".repeat(64),bytes:100}]};
  vi.stubGlobal("AudioContext",class { decodeAudioData=vi.fn(async()=>({duration:10})); close=vi.fn(async()=>{}); });
  vi.stubGlobal("crypto",{subtle:{digest:vi.fn(async()=>new ArrayBuffer(32))}});
  vi.stubGlobal("fetch",vi.fn(async(input: string)=>input.endsWith(id) ? metadata(decay)
    : input.endsWith("synth-decay") ? {ok:false,status:503} : {ok:true,arrayBuffer:async()=>new ArrayBuffer(100)}));
  render(<ListeningTrial trialId={id}/>);
  expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 503");
  expect(screen.getByRole("button",{name:"比較を再生"})).toBeDisabled();
  expect(screen.getByRole("button",{name:"採譜再生（固定音色）"})).toBeDisabled();
});
