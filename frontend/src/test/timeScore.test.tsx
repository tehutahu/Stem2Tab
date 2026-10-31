// @vitest-environment node
import { expect,it } from "vitest";
import { importer,midi,Settings } from "@coderline/alphatab";
import { renderToStaticMarkup } from "react-dom/server";
import { applyBassTab } from "../utils/scoreTab";
import { buildTimeScore,timeCursor,timeX,TIME_RATE } from "../utils/timeScore";
import TimeScoreView from "../components/TimeScoreView";

function synthetic(tempo=60){
  const note=(duration:number,tie="")=>`<note><pitch><step>E</step><octave>1</octave></pitch><duration>${duration}</duration><voice>1</voice>${tie?`<notations><tied type="${tie}"/></notations>`:""}</note>`;
  return importer.ScoreLoader.loadScoreFromBytes(new TextEncoder().encode(`<score-partwise version="4.0"><part-list><score-part id="P"><part-name>Synthetic</part-name></score-part></part-list><part id="P"><measure number="1"><attributes><divisions>100</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>F</sign><line>4</line></clef></attributes><direction><sound tempo="${tempo}"/></direction><note><rest/><duration>17</duration><voice>1</voice></note>${note(49)}<note><rest/><duration>25</duration><voice>1</voice></note>${note(209)}${note(100,"start")}</measure><measure number="2">${note(17,"stop")}${note(1)}<note><rest/><duration>382</duration><voice>1</voice></note></measure></part></score-partwise>`));
}
function performance(score:ReturnType<typeof synthetic>){const file=new midi.MidiFile();new midi.MidiFileGenerator(score,new Settings(),new midi.AlphaSynthMidiFileHandler(file)).generate();return file.events;}

it.each([60,120])("moves at 180 px/s through dense attacks, long notes, rests and barlines at %s BPM",tempo=>{
  const score=synthetic(tempo),layout=buildTimeScore(score,false);
  for(let seconds=0;seconds+.25<layout.total;seconds+=.25){
    if(Math.floor(seconds/4)!==Math.floor((seconds+.25)/4))continue;
    const a=timeCursor(layout,seconds*960*tempo/60)!,b=timeCursor(layout,(seconds+.25)*960*tempo/60)!;
    expect(b.x-a.x).toBeCloseTo(45,10);
  }
  for(const n of layout.notes){const p=timeCursor(layout,n.start*960*tempo/60)!;expect(p.x).toBeCloseTo(timeX(n.start,Math.floor(n.start/4)),10);}
  expect(TIME_RATE).toBe(180);
});
it.each([false,true])("does not mutate playback, preserves ties/one-tick repeated notes, TAB=%s",tab=>{
  const score=synthetic();if(tab)applyBassTab(score,{tuning:[28,33,38,43],maxFret:24});
  const before=performance(score),layout=buildTimeScore(score,tab);
  expect(performance(score)).toEqual(before);
  expect(layout.notes).toHaveLength(4);
  const expected=[[.17,.66],[.91,3],[3,4.17],[4.17,4.18]];
  layout.notes.forEach((n,i)=>{expect(n.start).toBeCloseTo(expected[i][0],10);expect(n.end).toBeCloseTo(expected[i][1],10);});
  const rests=[[0,.17],[.66,.91],[4.18,8]];expect(layout.rests).toHaveLength(rests.length);
  layout.rests.forEach((r,i)=>{expect(r.start).toBeCloseTo(rests[i][0],10);expect(r.end).toBeCloseTo(rests[i][1],10);});
  const html=renderToStaticMarkup(<TimeScoreView layout={layout}/>);
  expect(html.match(/data-time-onset=/g)).toHaveLength(4);
  expect(html.match(/data-attack="false"/g)).toHaveLength(1);
  expect(html.match(/data-tab-onset=/g)?.length??0).toBe(tab?4:0);
});
it("wraps only at four seconds and has no previous-position history for stop/seek/replay",()=>{
  const layout=buildTimeScore(synthetic(),false),tick=(s:number)=>s*960;
  expect(timeCursor(layout,tick(3.999))!.x).toBeGreaterThan(799);
  expect(timeCursor(layout,tick(4))!.x).toBe(80);
  expect(timeCursor(layout,tick(4))!.y).toBeGreaterThan(timeCursor(layout,0)!.y);
  const expected=timeCursor(layout,tick(1.23));timeCursor(layout,tick(7.9));timeCursor(layout,0);
  expect(timeCursor(layout,tick(1.23))).toEqual(expected);
  expect(timeCursor(layout,0)!.x).toBe(80);expect(timeCursor(layout,tick(8))!.x).toBe(800);
  expect(timeCursor(layout,NaN)).toBeNull();
});
it.each([[28,33,38,43],[23,28,33,38,43],[23,28,33,38,43,48]])("uses every saved TAB string without moving pitch or time: %j",(...tuning)=>{
  const score=synthetic();applyBassTab(score,{tuning,maxFret:24});
  const layout=buildTimeScore(score,true);expect(layout.tuning).toEqual([...tuning].reverse());
  for(const n of layout.notes)expect(tuning[n.string-1]+n.fret).toBe(n.pitch);
  expect(timeCursor(layout,0)!.y+timeCursor(layout,0)!.height).toBeGreaterThan(layout.tabTop+(tuning.length-1)*16);
});
it("rejects unknown meter, repeats, broken ties, and unassigned TAB rather than silently flattening them",()=>{
  let score=synthetic();score.masterBars[1].timeSignatureNumerator=3;expect(()=>buildTimeScore(score,false)).toThrow(/固定4\/4/);
  score=synthetic();score.masterBars[1].repeatCount=2;expect(()=>buildTimeScore(score,false)).toThrow(/反復/);
  score=synthetic();score.masterBars[1].tempoAutomations.push({...score.masterBars[0].tempoAutomations[0],value:90});expect(()=>buildTimeScore(score,false)).toThrow(/固定4\/4/);
  score=synthetic();score.tracks[0].staves[0].bars[1].voices[0].beats[0].notes[0].tieOrigin=null;expect(()=>buildTimeScore(score,false)).toThrow(/タイ/);
  score=synthetic();expect(()=>buildTimeScore(score,true)).toThrow(/TAB/);
});
