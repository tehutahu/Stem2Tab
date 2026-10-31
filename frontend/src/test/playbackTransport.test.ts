import { afterEach, expect, it, vi } from "vitest";
import { PlaybackTransport } from "../utils/playbackTransport";

const lives: PlaybackTransport[]=[];
afterEach(()=>{lives.forEach(t=>t.dispose());lives.length=0;vi.useRealTimers();});
function setup(){
  const driver={play:vi.fn(()=>true),pause:vi.fn(),stop:vi.fn(),seek:vi.fn()};
  const states:any[]=[],fail=vi.fn();
  const t=new PlaybackTransport(driver,s=>states.push(s),fail);lives.push(t);
  const pos=(ms:number,isSeek=false)=>t.positionChanged({currentTime:ms,currentTick:ms*1.92,endTime:12000,isSeek});
  const state=(state:number,stopped=false)=>t.stateChanged({state,stopped});
  pos(0,true);
  const play=()=>{t.request('play');pos(t.snapshot.time,true);state(1);};
  return {t,driver,states,fail,pos,state,play};
}
it('pins pause immediately, rejects beat rewind and samples, and restores the exact resume anchor',()=>{
  const {t,driver,pos,state,play}=setup();play();pos(5180);t.request('pause');
  pos(5210);pos(4650,true);state(0);pos(4650,true);
  expect(t.snapshot).toMatchObject({mode:'paused',time:5180,tick:9945.6,pending:true});
  expect(driver.seek).toHaveBeenLastCalledWith(5180);
  pos(5180,true);pos(5220);state(1);
  expect(t.snapshot).toMatchObject({mode:'paused',time:5180,pending:false});
  play();pos(5190);expect(t.snapshot).toMatchObject({mode:'playing',time:5190,pending:false});
});
it('a newer stop is never overwritten by a pending pause or its restored position',()=>{
  const {t,driver,pos,state,play}=setup();play();pos(3500);t.request('pause');t.request('stop');
  state(0);expect(driver.stop).toHaveBeenCalledTimes(1);pos(3500,true);pos(0,true);state(0,true);pos(3600);state(1);
  expect(t.snapshot).toMatchObject({mode:'stopped',time:0,tick:0,pending:false});
});
it('coalesces rapid seeks behind the acknowledgement barrier and ignores intermediate replies',()=>{
  const {t,driver,pos,state,play}=setup();play();pos(5000);t.request('seek',8000);t.request('seek',2000);
  state(0);expect(driver.seek).toHaveBeenLastCalledWith(2000);pos(8000,true);pos(1000,true);
  expect(t.snapshot).toMatchObject({time:2000,pending:true});pos(2000,true);state(1);pos(2010);
  expect(t.snapshot).toMatchObject({mode:'playing',time:2010,pending:false});
});
it('seeks while stopped without letting a delayed stop notification erase the target',()=>{
  const {t,pos,state}=setup();t.request('stop');t.request('seek',3500);pos(0,true);state(0,true);
  pos(3500,true);state(0,true);pos(0,true);
  expect(t.snapshot).toMatchObject({mode:'stopped',time:3500,tick:6720,pending:false});
});
it('does not resume before the pause seek barrier and lets the latest replay win',()=>{
  const {t,driver,pos,state,play}=setup();play();pos(6100);t.request('pause');t.request('play');
  state(0);expect(driver.play).toHaveBeenCalledTimes(1);pos(6000,true);expect(driver.play).toHaveBeenCalledTimes(1);
  pos(6100,true);expect(driver.play).toHaveBeenCalledTimes(2);state(0);expect(t.snapshot.pending).toBe(true);state(1);
  expect(t.snapshot).toMatchObject({mode:'playing',time:6100,pending:false});
});
it('pins natural completion at zero including buffered trailing samples',()=>{
  const {t,pos,state,play}=setup();play();pos(12000);pos(0,true);state(0,true);pos(12010);state(1);
  expect(t.snapshot).toMatchObject({mode:'stopped',time:0,tick:0,pending:false});
});
it('ignores stale stopped state and reverse positions during continuous playback',()=>{
  const {t,pos,state,play}=setup();play();pos(6000);state(0,true);pos(5500);pos(0,true);
  expect(t.snapshot).toMatchObject({mode:'playing',time:6000});
});
it('handles synchronous driver acknowledgements without losing the active phase',()=>{
  const {t,driver,pos,state}=setup();driver.seek.mockImplementation(ms=>pos(ms,true));driver.play.mockImplementation(()=>{state(1);return true;});
  driver.pause.mockImplementation(()=>{pos(0,true);state(0);});driver.stop.mockImplementation(()=>{pos(0,true);state(0,true);});
  t.request('play');pos(1234);t.request('pause');expect(t.snapshot).toMatchObject({mode:'paused',time:1234,pending:false});
  t.request('play');t.request('stop');expect(t.snapshot).toMatchObject({mode:'stopped',time:0,pending:false});
});
it('fails explicitly on a missing acknowledgement and ignores subsequent callbacks',()=>{
  vi.useFakeTimers();const {t,fail,pos,state}=setup();t.request('play');vi.advanceTimersByTime(8001);
  expect(fail).toHaveBeenCalledOnce();expect(String(fail.mock.calls[0][0])).toContain('acknowledgement timed out');
  const before=t.snapshot;pos(1000,true);state(1);expect(t.snapshot).toEqual(before);vi.useRealTimers();
});
it.each(['false','throw'])('reports play %s failure',kind=>{
  const {t,driver,fail,pos}=setup();driver.play.mockImplementation(()=>{if(kind==='throw')throw Error('play failed');return false;});
  t.request('play');pos(0,true);expect(fail).toHaveBeenCalledOnce();
});
it('disposal cancels pending timeouts and ignores events from replaced scores',()=>{
  vi.useFakeTimers();const {t,fail,pos,state}=setup();t.request('play');t.dispose();const before=t.snapshot;
  vi.advanceTimersByTime(9000);pos(1000,true);state(1);expect(fail).not.toHaveBeenCalled();expect(t.snapshot).toEqual(before);vi.useRealTimers();
});
