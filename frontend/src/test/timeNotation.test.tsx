// @vitest-environment node
import {expect,it} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {rhythmValue,buildTimeNotation,beamGroups,stemDirection} from "../utils/timeNotation";
import {staffPitch,timeCursor,type TimeScore} from "../utils/timeScore";
import TimeScoreView from "../components/TimeScoreView";

function fixture(tempo=120):TimeScore {
  const notes=[{start:0,end:.25},{start:.25,end:.5},{start:1,end:1.75},{start:1.75,end:4.5},{start:4.5,end:4.51}].map(n=>({...n,pitch:28,string:1,fret:0,...staffPitch(28)}));
  return {tempo,total:8,notes,tuning:[43,38,33,28],bars:Array.from({length:4},(_,index)=>({index,start:index*2,end:index*2+2})),rests:[{start:.5,end:1},{start:4.51,end:8}],rows:2,rowHeight:310,staffBottom:120,tabTop:210,contentBottom:270};
}
it.each([4,2,1,.5,.25,.125,.0625,1.5,.75,1.75])("represents standard and dotted quarter lengths %s exactly",q=>{
  const r=rhythmValue(q);expect(r.actual).toBe(1);expect(r.normal).toBe(1);expect(4/r.denominator*(2-1/2**r.dots)).toBe(q);
});
it.each([.34,.16,.98,1.06,.02,3.86])("uses an exact explicit tuplet ratio for raw duration %s without rounding",q=>{
  const r=rhythmValue(q);expect(r.actual).not.toBe(r.normal);expect(4/r.denominator*r.normal/r.actual).toBeCloseTo(q,10);
});
it("preserves attacks, pitches, segment time and tie chains across bar and row boundaries",()=>{
  const layout=fixture(),before=JSON.stringify(layout),n=buildTimeNotation(layout);
  expect(JSON.stringify(layout)).toBe(before);expect(n.notes.filter(s=>s.attack)).toHaveLength(5);
  for(const original of layout.notes){const segments=n.notes.filter(s=>s.note===original);expect(segments[0].start).toBe(original.start);expect(segments.at(-1)!.end).toBe(original.end);expect(segments.reduce((v,s)=>v+s.end-s.start,0)).toBeCloseTo(original.end-original.start,10);for(const s of segments)expect(s.rhythm.quarters*60/layout.tempo).toBeCloseTo(s.end-s.start,10);}
  expect(n.notes.filter(s=>s.tieBefore)).toHaveLength(2);expect(n.notes.filter(s=>s.tieAfter)).toHaveLength(2);
  expect(beamGroups(n.notes,120)[0].map(s=>s.start)).toEqual([0,.25]);
  expect(timeCursor(layout,.25*1920)!.x-timeCursor(layout,0)!.x).toBe(45);
});
it("renders notation, flags, beams, dots, rests, ties and TAB without duration bars",()=>{
  const html=renderToStaticMarkup(<TimeScoreView layout={fixture()}/>);
  expect(html.match(/data-time-onset=/g)).toHaveLength(5);expect(html.match(/data-tab-onset=/g)).toHaveLength(5);
  for(const attribute of ['data-note-stem','data-note-flag','data-note-beam','data-note-tie','data-time-rest','data-tuplet','data-tab-tie'])expect(html).toContain(attribute);
  expect(html).not.toContain('#26768b');expect(html).not.toContain('実時間長');
});
it("does not beam across a rest, bar, quarter boundary, tuplet or overlapping note",()=>{
  const ns=buildTimeNotation(fixture()).notes;
  expect(beamGroups(ns,120)).toHaveLength(1);
  const altered=ns.map(n=>({...n}));altered[1].start=.3;expect(beamGroups(altered,120)).toHaveLength(0);
  altered[1].start=0;expect(beamGroups(altered,120)).toHaveLength(0);
});
it("rejects nonpositive or unrepresentable notation lengths explicitly",()=>{
  for(const q of [0,NaN,-1,1e-9])expect(()=>rhythmValue(q)).toThrow();
});
it("turns a short pre-barline tie fragment downward without displacing its attack",()=>{
  const layout=fixture();layout.notes[3].start=1.92;
  const n=buildTimeNotation(layout).notes.find(s=>s.start===1.92)!;
  expect(stemDirection(n)).toBe(-1);expect(n.start).toBe(1.92);expect(n.end).toBe(2);
});
