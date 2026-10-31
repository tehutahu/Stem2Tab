// @vitest-environment node
import { describe, expect, it } from "vitest";
import { importer, model, rendering, Settings, type AlphaTabApi } from "@coderline/alphatab";
import { buildCursorBars, cursorAtTick } from "../utils/scoreCursor";
import { applyBassTab } from "../utils/scoreTab";
import { readBassTabConfig } from "../utils/bassTabConfig";

// Synthetic performance timing, no private MIDI or reference music in fixtures.
function scoreFromXml(pitch="E", octave=1): model.Score {
  const note=(duration:number,tie="")=>`<note><pitch><step>${pitch}</step><octave>${octave}</octave></pitch><duration>${duration}</duration><voice>1</voice>${tie?`<notations><tied type="${tie}"/></notations>`:""}</note>`;
  return importer.ScoreLoader.loadScoreFromBytes(new TextEncoder().encode(`<score-partwise version="4.0"><part-list><score-part id="P"><part-name>Synthetic</part-name></score-part></part-list><part id="P">
  <measure number="1"><attributes><divisions>100</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>F</sign><line>4</line></clef></attributes><direction><sound tempo="60"/></direction>
  <note><rest/><duration>17</duration><voice>1</voice></note>${note(49)}<note><rest/><duration>25</duration><voice>1</voice></note>${note(209)}${note(100,"start")}</measure>
  <measure number="2">${note(17,"stop")}${note(1)}<note><rest/><duration>382</duration><voice>1</voice></note></measure></part></score-partwise>`));
}
function beats(score:model.Score) { return score.tracks.flatMap(t=>t.staves.flatMap(s=>s.bars.flatMap(b=>b.voices.flatMap(v=>v.beats)))); }
function renderPaths(score:model.Score,width=960) {
  const settings=new Settings();settings.core.engine="svg";settings.core.enableLazyLoading=false;
  const renderer=new rendering.ScoreRenderer(settings);renderer.width=width;renderer.renderScore(score,[0]);
  const tickCache={masterBars:score.masterBars.map(b=>({masterBar:b,start:b.start,end:b.start+b.calculateDuration()}))};
  return buildCursorBars({renderer,tickCache} as unknown as Pick<AlphaTabApi,"renderer"|"tickCache">);
}
describe("absolute playback cursor",()=>{
  it("stays between the actual time anchors through rests, short notes and tied bar crossings",()=>{
    const score=scoreFromXml(),bars=renderPaths(score);
    expect(bars).toHaveLength(2);
    for (const bar of bars) {
      let x=-Infinity;
      for(let t=bar.start;t<bar.end;t+=3.7){const pos=cursorAtTick(bars,t)!;expect(pos.x).toBeGreaterThanOrEqual(x);x=pos.x;expect(pos.x).toBeLessThanOrEqual(bar.points[bar.points.length-1].x);}
      for(const p of bar.points.slice(0,-1)) expect(cursorAtTick(bars,p.tick)!.x).toBeCloseTo(p.x,7);
    }
    expect(cursorAtTick(bars,3840)!.bar).toBe(1);
    expect(cursorAtTick(bars,18/100*960+3840)!.x).toBeCloseTo(bars[1].points[2].x,7);
  });
  it("seeks, stops and repeats without retaining the previous animation position",()=>{
    const bars=renderPaths(scoreFromXml());
    const expected=cursorAtTick(bars,1200);
    cursorAtTick(bars,7400);cursorAtTick(bars,0);
    expect(cursorAtTick(bars,1200)).toEqual(expected);
    expect(cursorAtTick(bars,Infinity)).toBeNull();
  });
  it("rebuilds from layout after reflow and jumps left only at the new system",()=>{
    const score=scoreFromXml(),wide=renderPaths(score,1200),narrow=renderPaths(score,350);
    expect(narrow[1].y).toBeGreaterThan(narrow[0].y);
    expect(cursorAtTick(narrow,4000)!.x).not.toBe(cursorAtTick(wide,4000)!.x);
  });
  it("coalesces simultaneous voices and rejects genuinely reversed coordinates",()=>{
    const masterBar={index:0};
    const points=[{t:0,x:20},{t:0,x:21},{t:240,x:50},{t:480,x:80}];
    const api={tickCache:{masterBars:[{masterBar,start:0,end:960}]},renderer:{boundsLookup:{findMasterBar:()=>({visualBounds:{x:0,y:10,w:100,h:80},bars:[{beats:points.map(p=>({beat:{playbackStart:p.t},onNotesX:p.x}))}]})}}};
    expect(buildCursorBars(api as any)[0].points).toHaveLength(4);
    points[2].x=10;expect(()=>buildCursorBars(api as any)).toThrow(/逆行/);
  });
});
describe("original-time staff plus TAB",()=>{
  it.each([[28,33,38,43],[23,28,33,38,43],[23,28,33,38,43,48]])("preserves exact pitches/times/ties with %j tuning",(...tuning)=>{
    const score=scoreFromXml();
    const snapshot=()=>beats(score).map(b=>[b.absolutePlaybackStart,b.playbackDuration,b.notes.map(n=>[n.realValue,n.isTieDestination])]);
    const original=snapshot();const count=applyBassTab(score,{tuning,maxFret:24});
    expect(count).toBe(4);expect(snapshot()).toEqual(original);
    for(const b of beats(score))for(const n of b.notes) expect(tuning[n.string-1]+n.fret).toBe(n.realValue);
    expect(score.tracks[0].staves[0].showTablature).toBe(true);
    expect(renderPaths(score)).toHaveLength(2);
  });
  it("rejects out-of-range notes before changing the score",()=>{
    const score=scoreFromXml("B",0),before=score.tracks[0].staves[0].tuning;
    expect(()=>applyBassTab(score,{tuning:[28,33,38,43],maxFret:24})).toThrow(/音域外/);
    expect(score.tracks[0].staves[0].tuning).toBe(before);
  });
  it("rejects overlapping assignments rather than dropping or moving notes",()=>{
    const score=scoreFromXml();const voice=score.tracks[0].staves[0].bars[0].voices[0];
    voice.beats[3].playbackStart=voice.beats[1].playbackStart;
    expect(()=>applyBassTab(score,{tuning:[28,33,38,43],maxFret:24})).toThrow(/同じ弦/);
  });
  it("rejects missing, mismatched and invalid saved tuning",()=>{
    expect(readBassTabConfig({})).toBeNull();
    expect(readBassTabConfig({strings:4,tuning_midi:[43,38,33,28],max_fret:24})).toBeNull();
    expect(readBassTabConfig({strings:4,tuning_midi:[28,33,38,43],max_fret:24})).toEqual({tuning:[28,33,38,43],maxFret:24});
  });
});
