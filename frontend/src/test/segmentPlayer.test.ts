import { expect, it, vi } from "vitest";
import { SegmentPlayer } from "../utils/segmentPlayer";

function setup() {
  const nodes: any[]=[]; const states: any[]=[];
  const context: any={currentTime:0,state:"running",resume:vi.fn(async()=>{}),destination:{},createBufferSource:()=>{
    const n:any={connect:vi.fn(),disconnect:vi.fn(),start:vi.fn(),stop:vi.fn(),onended:null};nodes.push(n);return n;
  }};
  const buffer:any={duration:10};
  const player=new SegmentPlayer(context,{original:buffer,bass:buffer,synth:buffer},10,s=>states.push(s));
  return {player,context,nodes,states};
}
it("switches while playing at the same source position and stops the previous voice",async()=>{
  const {player,context,nodes}=setup();player.configure(1,4,false);await player.play();context.currentTime=1.25;
  await player.select("bass");expect(nodes).toHaveLength(2);expect(nodes[0].onended).toBeNull();expect(nodes[1].start).toHaveBeenCalledWith(0,2.25);
  expect(player.snapshot().track).toBe("bass");player.stop();expect(player.snapshot()).toMatchObject({playing:false,position:1});
});
it("uses audio-clock loop boundaries and preserves position on pause",async()=>{
  const {player,context,nodes}=setup();player.configure(1,2,true);await player.play();context.currentTime=2.4;
  expect(player.snapshot().position).toBeCloseTo(1.4);expect(nodes[0]).toMatchObject({loop:true,loopStart:1,loopEnd:2});
  player.pause();context.currentTime=8;expect(player.snapshot().position).toBeCloseTo(1.4);
});
it("does not start a late resume after stop, reconfiguration or disposal",async()=>{
  for(const action of ["stop","configure","dispose"] as const){const {player,context,nodes}=setup();let finish!:()=>void;
    context.resume.mockImplementation(()=>new Promise<void>(r=>{finish=r}));const pending=player.play();
    if(action==="configure")player.configure(1,3,false);else player[action]();finish();await pending;expect(nodes).toHaveLength(0);
  }
});
it("ignores stale end callbacks after switching and rejects interval errors",async()=>{
  const {player,context,nodes}=setup();await player.play();const stale=nodes[0].onended;context.currentTime=2;
  await player.select("synth");stale();expect(player.snapshot().playing).toBe(true);
  expect(()=>player.configure(2,1,false)).toThrow();expect(()=>player.configure(0,11,false)).toThrow();expect(()=>player.configure(0,.01,false)).toThrow();
  expect(()=>player.seek(NaN)).toThrow();expect(()=>player.seek(11)).toThrow();
});
it("ends at the exact segment end and can replay from segment start",async()=>{
  const {player,context,nodes}=setup();player.configure(1,3,false);await player.play();expect(nodes[0].stop).toHaveBeenCalledWith(2);
  context.currentTime=2;nodes[0].onended();expect(player.snapshot()).toMatchObject({playing:false,position:3});
  await player.play();expect(nodes[1].start).toHaveBeenCalledWith(0,1);
});
it("handles rapid play calls, source selection and resume rejection without stale playback",async()=>{
  const {player,context,nodes}=setup();let finish!:()=>void;context.resume.mockImplementationOnce(()=>new Promise<void>(r=>{finish=r}));
  const first=player.play();await player.select("bass");await player.play();finish();await first;expect(nodes).toHaveLength(1);expect(player.snapshot().track).toBe("bass");
  context.resume.mockRejectedValueOnce(new Error("blocked"));await expect(player.play()).rejects.toThrow("blocked");expect(player.snapshot().playing).toBe(false);
});
