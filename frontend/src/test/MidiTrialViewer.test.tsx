import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import MidiTrialViewer from "../components/MidiTrialViewer";
const mock = vi.hoisted(() => ({ apis: [] as any[], loadResult: true }));
vi.mock("@coderline/alphatab", () => ({ model: { Clef: { F4: 3 } }, AlphaTabApi: class {
  scoreLoaded = event(); renderStarted = event(); renderFinished = event(); playerReady = event(); playerStateChanged = event(); playerPositionChanged = event(); error = event();
  settings = {display:{scale:1}}; updateSettings=vi.fn(); render=vi.fn();
  renderer = { boundsLookup: null }; tickCache = null;
  isReadyForPlayback = true; _time = 0;
  get timePosition(){return this._time;}
  set timePosition(value:number){this._time=value;this.playerPositionChanged.emit({currentTime:value,currentTick:value*1.92,endTime:12000,isSeek:true});}
  play = vi.fn(() => {this.playerStateChanged.emit({state:1,stopped:false});return true;});
  pause = vi.fn(() => {this.playerStateChanged.emit({state:0,stopped:false});});
  stop = vi.fn(() => {this.timePosition=0;this.playerStateChanged.emit({state:0,stopped:true});});
  destroy = vi.fn(); load = vi.fn(() => mock.loadResult);
  constructor() { mock.apis.push(this); }
} }));
function event(): { on: (handler: (value: any) => void) => void; emit: (value?: any) => void } { const handlers: Array<(v: any) => void> = []; return { on: (h) => handlers.push(h), emit: (v) => handlers.forEach((h) => h(v)) }; }
beforeEach(() => { mock.apis.length = 0; mock.loadResult = true; });
it("freezes pause position before delayed notifications and ignores a late beat-start seek",()=>{
  render(<MidiTrialViewer scoreUrl="/original.xml"/>);const api=mock.apis[0];
  act(()=>api.playerReady.emit());fireEvent.click(screen.getByRole('button',{name:'再生'}));
  act(()=>api.playerPositionChanged.emit({currentTime:5180,currentTick:9945.6,endTime:12000}));
  fireEvent.click(screen.getByRole('button',{name:'一時停止'}));
  act(()=>{api.playerPositionChanged.emit({currentTime:4650,currentTick:8928,endTime:12000,isSeek:true});api.playerPositionChanged.emit({currentTime:5250,currentTick:10080,endTime:12000});});
  expect(screen.getByRole('slider')).toHaveValue('5.18');expect(screen.getByRole('status')).toHaveTextContent('一時停止');
  fireEvent.click(screen.getByRole('button',{name:'停止'}));expect(screen.getByRole('slider')).toHaveValue('0');
});
it("destroys the old player on spacing switches and rejects stale completion/position events",()=>{
  const view=render(<MidiTrialViewer scoreUrl="/original.xml"/>),old=mock.apis[0];
  act(()=>old.playerReady.emit());fireEvent.click(screen.getByRole("button",{name:"再生"}));
  view.rerender(<MidiTrialViewer scoreUrl="/original.xml" spacing="time"/>);
  expect(old.destroy).toHaveBeenCalledTimes(1);
  act(()=>{old.renderFinished.emit();old.playerReady.emit();old.playerPositionChanged.emit({currentTime:5000,endTime:12000,currentTick:4800});});
  expect(screen.getByRole("button",{name:"再生"})).toBeDisabled();expect(screen.getByText("0.00 / 0.00 秒")).toBeVisible();
  expect(screen.getByText(/小節ごとに画面幅/)).toHaveTextContent("同じ小節内では等速");
  view.rerender(<MidiTrialViewer scoreUrl="/original.xml"/>);expect(mock.apis[1].destroy).toHaveBeenCalledTimes(1);
});
it("keeps notation-clock rebarring distinct from quantization and disposes stale players",()=>{
  const view=render(<MidiTrialViewer scoreUrl="/normal.xml"/>);const old=mock.apis[0];
  act(()=>old.playerReady.emit());fireEvent.click(screen.getByRole("button",{name:"再生"}));
  view.rerender(<MidiTrialViewer scoreUrl="/double.xml" variant="notation-clock"/>);
  expect(old.destroy).toHaveBeenCalledTimes(1);
  act(()=>{old.playerReady.emit();old.playerPositionChanged.emit({currentTime:6000,endTime:12000,currentTick:5760});});
  expect(screen.getByText(/記譜テンポ2倍の候補版/)).toHaveTextContent("量子化ではなく");
  expect(screen.queryByText(/利用者が明示したBPM/)).toBeNull();
  expect(screen.getByRole("button",{name:"再生"})).toBeDisabled();
  expect(screen.getByText("0.00 / 0.00 秒")).toBeVisible();
  view.rerender(<MidiTrialViewer scoreUrl="/normal.xml"/>);
  expect(mock.apis[1].destroy).toHaveBeenCalledTimes(1);
});
it("uses bass display clef only for explicit GP5 without modifying note data", () => {
  const view=render(<MidiTrialViewer scoreUrl="/bass.gp5" variant="explicit-gp5" />);
  const note={realValue:28,isTieDestination:false,string:4,fret:0};
  const bar={clef:4,voices:[{beats:[{notes:[note]}]}]};
  const staff={displayTranspositionPitch:-12,transpositionPitch:0,bars:[bar]};
  act(()=>mock.apis[0].scoreLoaded.emit({tracks:[{playbackInfo:{program:33},staves:[staff]}]}));
  expect(bar.clef).toBe(3);expect(staff.displayTranspositionPitch).toBe(0);expect(staff.transpositionPitch).toBe(0);expect(note).toEqual({realValue:28,isTieDestination:false,string:4,fret:0});
  view.rerender(<MidiTrialViewer scoreUrl="/storage.xml" />);bar.clef=4;
  act(()=>mock.apis[1].scoreLoaded.emit({tracks:[{playbackInfo:{program:0},staves:[{bars:[bar]}]}]}));
  expect(bar.clef).toBe(4);
});
it("latches load failure and ignores late readiness until score changes", () => {
  mock.loadResult = false;
  const view = render(<MidiTrialViewer scoreUrl="/bad" />); const old = mock.apis[0];
  act(() => old.playerReady.emit()); expect(screen.getByRole("button", { name: "再生" })).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("読込");
  mock.loadResult = true; view.rerender(<MidiTrialViewer scoreUrl="/good" />);
  act(() => mock.apis[1].playerReady.emit()); expect(screen.getByRole("button", { name: "再生" })).toBeEnabled();
});
it("stops an unsupported TAB view and ignores late ready/position events",()=>{
  render(<MidiTrialViewer scoreUrl="/one" bassTab={{tuning:[28,33,38,43],maxFret:24}}/>);
  const api=mock.apis[0];
  act(()=>{api.scoreLoaded.emit({tracks:[{staves:[{isPercussion:true}]}]});api.playerReady.emit();api.playerPositionChanged.emit({currentTime:9000,endTime:12000,currentTick:8640});});
  expect(screen.getByRole("alert")).toHaveTextContent("単一ベース譜");
  expect(screen.getByRole("button",{name:"再生"})).toBeDisabled();
  expect(screen.getByTestId("score-time-cursor")).not.toBeVisible();
});
it("keeps staff and playback when a saved TAB tuning cannot represent every note",()=>{
  render(<MidiTrialViewer scoreUrl="/range" bassTab={{tuning:[28,33,38,43],maxFret:24}}/>);
  const api=mock.apis[0];
  const notes=[{realValue:24,isTieDestination:false},{realValue:28,isTieDestination:false}];
  const staff={isPercussion:false,transpositionPitch:0,capo:0,bars:[{voices:[{beats:[{notes,absolutePlaybackStart:0,playbackDuration:960}]}]}]};
  act(()=>{api.scoreLoaded.emit({tracks:[{playbackInfo:{program:33},staves:[staff]}]});api.renderFinished.emit();api.playerReady.emit();});
  expect(screen.getByRole("note")).toHaveTextContent("音域外に1音");
  expect(screen.getByRole("note")).toHaveTextContent("MIDI 24");
  expect(screen.getByRole("note")).toHaveTextContent("五線譜と再生を保持");
  expect(screen.queryByText(/描画・再生エラー/)).toBeNull();
  expect(screen.getByRole("button",{name:"再生"})).toBeEnabled();
  fireEvent.click(screen.getByRole("button",{name:"再生"}));
  expect(api.play).toHaveBeenCalledOnce();
  expect(notes.map(n=>n.realValue)).toEqual([24,28]);
});
it("disposes previous playback when changing TAB settings and preserves a stable config",()=>{
  const config={tuning:[28,33,38,43],maxFret:24};
  const view=render(<MidiTrialViewer scoreUrl="/one" bassTab={config}/>);const old=mock.apis[0];
  view.rerender(<MidiTrialViewer scoreUrl="/one" bassTab={{...config,tuning:[...config.tuning]}}/>);
  expect(mock.apis).toHaveLength(1);
  view.rerender(<MidiTrialViewer scoreUrl="/one"/>);expect(old.destroy).toHaveBeenCalledTimes(1);
  act(()=>{old.playerReady.emit();old.playerPositionChanged.emit({currentTime:9000,endTime:12000,currentTick:8640});});
  expect(screen.getByRole("button",{name:"再生"})).toBeDisabled();
  expect(screen.getByTestId("score-time-cursor")).not.toBeVisible();
});
it.each(["false", "throw"])("shows play %s failure and blocks late readiness", (failure) => {
  render(<MidiTrialViewer scoreUrl="/one" />); const api = mock.apis[0];
  act(() => api.playerReady.emit());
  api.play.mockImplementation(() => { if (failure === "throw") throw new Error("play exception"); return false; });
  fireEvent.click(screen.getByRole("button", { name: "再生" }));
  expect(screen.getByRole("alert")).toBeVisible();
  act(() => api.playerReady.emit()); expect(screen.getByRole("button", { name: "再生" })).toBeDisabled();
});
it("does not reenable after a fatal SDK error and late playerReady", () => {
  render(<MidiTrialViewer scoreUrl="/one" />); const api = mock.apis[0];
  act(() => { api.playerReady.emit(); api.error.emit(new Error("fatal synth")); api.playerReady.emit(); });
  expect(screen.getByRole("alert")).toHaveTextContent("fatal synth");
  expect(screen.getByRole("button", { name: "再生" })).toBeDisabled();
});
it("gates playback and reports paused/stopped states and seek seconds", () => {
  render(<MidiTrialViewer scoreUrl="/one" />); const api = mock.apis[0]; expect(screen.getByRole("button", { name: "再生" })).toBeDisabled();
  act(() => { api.playerReady.emit(); api.renderFinished.emit(); api.playerPositionChanged.emit({ currentTime: 1000, endTime: 5000 }); });
  fireEvent.click(screen.getByRole("button", { name: "再生" })); expect(api.play).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button",{name:"一時停止"})); expect(screen.getByRole("status")).toHaveTextContent("一時停止");
  fireEvent.click(screen.getByRole("button",{name:"停止"})); expect(screen.getByRole("status")).toHaveTextContent("停止");
  fireEvent.change(screen.getByRole("slider"), { target: { value: "2" } }); expect(api.timePosition).toBe(2000);
});
it("destroys on score change/unmount and ignores old callbacks", () => {
  const view = render(<MidiTrialViewer scoreUrl="/one" />); const old = mock.apis[0];
  view.rerender(<MidiTrialViewer scoreUrl="/two" />); expect(old.destroy).toHaveBeenCalledTimes(1);
  act(() => { old.playerReady.emit(); old.playerStateChanged.emit({ state: 1 }); old.renderFinished.emit(); old.playerPositionChanged.emit({currentTime:7000,endTime:12000,currentTick:6720}); }); expect(screen.getByRole("button", { name: "再生" })).toBeDisabled();
  expect(screen.getByTestId("score-time-cursor")).not.toBeVisible();
  view.unmount(); expect(mock.apis[1].destroy).toHaveBeenCalledTimes(1);
});
it("counts logical attacks without tie continuations and reports pitches", () => {
  render(<MidiTrialViewer scoreUrl="/one" />);
  act(() => mock.apis[0].scoreLoaded.emit({ tracks: [{ playbackInfo: { program: 0 }, staves: [{ bars: [{ voices: [{ beats: [{ notes: [{ realValue: 28, isTieDestination: false }, { realValue: 28, isTieDestination: true }] }] }] }] }] }] }));
  expect(screen.getByText(/論理発音数 1/)).toHaveTextContent("E1");
});

it("reports SDK errors, refuses unready playback, and allows replay after stop", () => {
  render(<MidiTrialViewer scoreUrl="/one" />); const api = mock.apis[0];
  act(() => api.playerReady.emit()); api.isReadyForPlayback = false; fireEvent.click(screen.getByRole("button", { name: "再生" })); expect(api.play).not.toHaveBeenCalled();
  api.isReadyForPlayback = true; fireEvent.click(screen.getByRole("button", { name: "再生" })); fireEvent.click(screen.getByRole("button", { name: "一時停止" })); expect(api.pause).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "停止" })); expect(api.stop).toHaveBeenCalledTimes(1); act(() => api.playerStateChanged.emit({ state: 0, stopped: true })); fireEvent.click(screen.getByRole("button", { name: "再生" })); expect(api.play).toHaveBeenCalledTimes(2);
  act(() => api.error.emit(new Error("synth failed"))); expect(screen.getByRole("alert")).toHaveTextContent("synth failed"); expect(screen.getByRole("button", { name: "再生" })).toBeDisabled();
});

it("changes engraving size without destroying or resetting the current player", () => {
  render(<MidiTrialViewer scoreUrl="/original.xml" />);
  const api = mock.apis[0];
  act(() => api.playerReady.emit());
  fireEvent.click(screen.getByRole("button", { name: "再生" }));
  act(() => api.playerPositionChanged.emit({ currentTime: 2500, currentTick: 4800, endTime: 12000 }));
  fireEvent.change(screen.getByLabelText("譜面の大きさ"), { target: { value: "1.2" } });
  expect(api.settings.display.scale).toBe(1.2);
  expect(api.updateSettings).toHaveBeenCalledOnce();
  expect(api.render).toHaveBeenCalledOnce();
  expect(api.destroy).not.toHaveBeenCalled();
  expect(api.stop).not.toHaveBeenCalled();
  expect(screen.getByRole("slider")).toHaveValue("2.5");
  expect(screen.getByRole("region", { name: "譜面・横スクロール可能" })).toHaveAttribute("tabindex", "0");
});

it("labels user-declared clock previews as seconds-preserving without claiming quantization", () => {
  render(<MidiTrialViewer scoreUrl="/musical-clock.xml" variant="musical-clock" />);
  expect(screen.getByText(/利用者が明示した固定BPM/)).toHaveTextContent("量子化・自動推定は行いません");
  expect(screen.getByText(/利用者が明示した固定BPM/)).toHaveTextContent("素材先頭の時刻0");
  expect(screen.queryByText(/量子化別版です/)).toBeNull();
});


it("reports dense overflow after the SDK appends SVGs, and clears it after a narrower rerender", async () => {
  render(<MidiTrialViewer scoreUrl="/original.xml" />);
  const frame = screen.getByRole("region", { name: "譜面・横スクロール可能" });
  let width = 300;
  Object.defineProperties(frame, { clientWidth: { value: 300 }, scrollWidth: { get: () => width } });
  act(() => mock.apis[0].renderFinished.emit());
  expect(screen.queryByText(/音符が密な小節は/)).toBeNull();
  const svgHost = document.createElement("div");
  width = 900;
  frame.querySelector(".midi-trial-score")!.appendChild(svgHost);
  await waitFor(() => expect(screen.getByText(/音符が密な小節は/)).toBeVisible());
  width = 300;
  svgHost.style.width = "300px";
  await waitFor(() => expect(screen.queryByText(/音符が密な小節は/)).toBeNull());
});
