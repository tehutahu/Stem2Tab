import { expect, it, vi } from "vitest";
import { buildLibraryTimeCursor, installLibraryTimeLayout, LIBRARY_TIME_RATE } from "../utils/libraryTimeLayout";

function fixture(){
  const beats=[{absoluteDisplayStart:0,playbackStart:0,notes:[]},{absoluteDisplayStart:480,playbackStart:960,notes:[]},{absoluteDisplayStart:1920,playbackStart:2880,notes:[]}];
  const master={index:0,start:0,timeSignatureNumerator:4,timeSignatureDenominator:4,tempoAutomations:[],calculateDuration:()=>3840};
  const bar={voices:[{beats}]};
  const score={tempo:120,tracks:[{staves:[{isPercussion:false,transpositionPitch:0,tuning:[],bars:[bar]}]}],masterBars:[master]};
  const info:any={};
  const r={bar,layoutingInfo:info,_preBeatGlyphs:{width:50},_postBeatGlyphs:{width:8},x:99,y:99,scaleToWidth:vi.fn()};
  const system={masterBarsRenderers:[{masterBar:master,renderers:[r]}],_allStaves:[{barRenderers:[r],topSpacing:3,topOverflow:2}],width:0,accoladeWidth:12,finalizeSystem:vi.fn()};
  const layout={_fitSystem:vi.fn((_system:any)=>{})};const api:any={renderer:{instance:{layout,score}}};
  return {api,score,layout,system,r,info};
}
it('uses playback time for library columns, retaining glyph objects and score values',()=>{
  const {api,score,layout,system,r,info}=fixture();const before=JSON.stringify(score),pre=r._preBeatGlyphs,post=r._postBeatGlyphs;
  installLibraryTimeLayout(api);layout._fitSystem(system);
  expect([...info.buildOnTimePositions()]).toEqual([[0,50],[480,230],[1920,590]]);
  expect(r.scaleToWidth).toHaveBeenCalledWith(834);expect(system.width).toBe(846);expect(system.finalizeSystem).toHaveBeenCalledOnce();
  expect(r._preBeatGlyphs).toBe(pre);expect(r._postBeatGlyphs).toBe(post);expect(JSON.stringify(score)).toBe(before);
  layout._fitSystem(system);expect([...info.buildOnTimePositions()]).toEqual([[0,50],[480,230],[1920,590]]);
});
it('leaves other renderers untouched and rejects unsupported renderer shapes',()=>{
  const a=fixture(),b=fixture();const original=b.layout._fitSystem;installLibraryTimeLayout(a.api);expect(b.layout._fitSystem).toBe(original);
  expect(()=>installLibraryTimeLayout({renderer:{}} as any)).toThrow(/対応/);
  a.system.masterBarsRenderers.push(a.system.masterBarsRenderers[0]);expect(()=>a.layout._fitSystem(a.system)).toThrow(/1小節/);
});
it('rejects tempo/meter changes instead of guessing a timeline',()=>{
  const {api,layout,system,score}=fixture();installLibraryTimeLayout(api);score.masterBars[0].timeSignatureNumerator=3;
  expect(()=>layout._fitSystem(system)).toThrow(/固定4\/4/);
});
function cursorFixture(secondX=280):any{
  const master={index:0};
  return {tickCache:{masterBars:[{masterBar:master,start:0,end:3840}]},renderer:{boundsLookup:{findMasterBar:()=>({visualBounds:{x:0,y:50,w:900,h:130},bars:[{beats:[{beat:{playbackStart:0},onNotesX:100},{beat:{playbackStart:960},onNotesX:secondX}]}]})}}};
}
it('uses measured glyph origins and a constant time rate through rests and the bar end',()=>{
  const bars=buildLibraryTimeCursor(cursorFixture(),120);expect(bars[0].points).toEqual([{tick:0,x:100},{tick:3840,x:100+2*LIBRARY_TIME_RATE}]);
});
it('refuses a rendered column that is not at its actual playback time',()=>{
  expect(()=>buildLibraryTimeCursor(cursorFixture(300),120)).toThrow(/一致しません/);
});
it('also validates simultaneous TAB columns rather than hiding their offset during deduplication',()=>{
  const api=cursorFixture(),bounds=api.renderer.boundsLookup.findMasterBar();
  bounds.bars.push({beats:[{beat:{playbackStart:0},onNotesX:101}]});
  api.renderer.boundsLookup.findMasterBar=()=>bounds;
  expect(()=>buildLibraryTimeCursor(api,120)).toThrow(/五線譜とTAB/);
});
