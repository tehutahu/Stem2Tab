import { memo } from "react";
import { ROW_SECONDS, TIME_LEFT, TIME_WIDTH, TIME_RATE, timeX, type TimeScore } from "../utils/timeScore";
import { buildTimeNotation, beamGroups, flags, stemDirection, type RhythmValue, type NotationNote } from "../utils/timeNotation";
import { pitchName } from "../utils/midiTrial";

function Ratio({r,x,endX,y}:{r:RhythmValue;x:number;endX:number;y:number}) {
  if(r.actual===r.normal)return null;
  const right=Math.max(x+16,endX-6),middle=(x+right)/2;
  return <g data-tuplet={`${r.actual}:${r.normal}`}>
    <path d={`M${x-5} ${y+3} v-5 H${right} v5`} fill="none" stroke="#555" strokeWidth={.8}/>
    <rect x={middle-19} y={y-11} width={38} height={11} fill="white"/>
    <text x={middle} y={y-2} textAnchor="middle" fontSize={10}>{r.actual}:{r.normal}</text>
  </g>;
}

/** Musical glyphs at exact performed times, independent of the audio/score data. */
function TimeScoreView({layout}:{layout:TimeScore}):JSX.Element {
  const notation=buildTimeNotation(layout),beams=beamGroups(notation.notes,layout.tempo);
  const tips=new Map<NotationNote,number>();
  for(const group of beams) {
    const dir=stemDirection(group[0]);
    const tip=dir===1?Math.min(...group.map(n=>layout.staffBottom+n.note.staffOffset))-34:Math.max(...group.map(n=>layout.staffBottom+n.note.staffOffset))+34;
    for(const n of group)tips.set(n,tip);
  }
  return <svg className="time-score" width={880} height={layout.rows*layout.rowHeight} role="img" aria-label="時間比例の五線譜とTAB。音符・休符・タイ、1秒180ピクセル、1段4秒">
    {Array.from({length:layout.rows},(_,row)=>{
      const start=row*ROW_SECONDS,end=Math.min(layout.total,start+ROW_SECONDS),base=row*layout.rowHeight;
      const x=(s:number)=>timeX(s,row),quarter=60/layout.tempo;
      const beatTimes=Array.from({length:Math.ceil(end/quarter)+1},(_,i)=>i*quarter).filter(t=>t>=start-1e-8&&t<=end+1e-8);
      const rowNotes=notation.notes.filter(n=>n.row===row);
      return <g key={row} data-time-row={row} transform={`translate(0,${base})`}>
        <text x={TIME_LEFT} y={18} fontSize={13}>{start.toFixed(1)}–{end.toFixed(1)} 秒　♩={layout.tempo}（表記基準）</text>
        {beatTimes.map(t=><g key={t}><line x1={x(t)} x2={x(t)} y1={32} y2={layout.contentBottom} stroke="#e6e6e6" strokeDasharray="2 3"/><text x={x(t)} y={31} textAnchor="middle" fontSize={10} fill="#555">{t.toFixed(1)}</text></g>)}
        {Array.from({length:5},(_,i)=><line key={i} x1={TIME_LEFT} x2={TIME_LEFT+TIME_WIDTH} y1={layout.staffBottom-i*10} y2={layout.staffBottom-i*10} stroke="#888"/>)}
        <text x={30} y={layout.staffBottom-10} fontSize={34}>𝄢</text><text x={49} y={layout.staffBottom-22} fontSize={16}>4</text><text x={49} y={layout.staffBottom-5} fontSize={16}>4</text>
        {layout.tuning.map((p,i)=><g key={i}><line x1={TIME_LEFT} x2={TIME_LEFT+TIME_WIDTH} y1={layout.tabTop+i*16} y2={layout.tabTop+i*16} stroke="#aaa"/><text x={22} y={layout.tabTop+i*16+4} fontSize={12}>{pitchName(p)}</text></g>)}
        {layout.bars.filter(b=>b.start>=start-1e-8&&b.start<end-1e-8).map(b=><g key={b.index}><line x1={x(b.start)} x2={x(b.start)} y1={38} y2={layout.contentBottom} stroke="#777"/><text x={x(b.start)+4} y={48} fontSize={10} fill="#555">小節 {b.index+1}</text></g>)}
        {notation.rests.filter(r=>r.row===row).map((r,i)=>{
          const crowded=rowNotes.some(n=>Math.abs(n.start-r.start)*TIME_RATE<25&&n.note.staffOffset>-35&&n.note.staffOffset<30);
          const ry=layout.staffBottom-20-(crowded?40:0),nx=x(r.start),code=58595+Math.log2(r.rhythm.denominator);
          return <g key={i} data-time-rest data-start={r.start} data-end={r.end}>
            <title>{`休符 ${r.start.toFixed(4)}–${r.end.toFixed(4)}秒`}</title>
            <text x={nx-5} y={ry} className="time-music-glyph" fontFamily={layout.fontFamily} fontSize={40}>{String.fromCodePoint(code)}</text>
            {Array.from({length:r.rhythm.dots},(_,d)=><circle key={d} cx={nx+11+d*5} cy={ry-5} r={1.5}/>)}
            <Ratio r={r.rhythm} x={nx} endX={x(r.end)} y={ry-20}/>
          </g>;
        })}
        {rowNotes.map((n,i)=>{
          const nx=x(n.start),ex=x(n.end),ny=layout.staffBottom+n.note.staffOffset,dir=stemDirection(n),tip=tips.get(n)??ny-dir*34;
          const stemX=nx+dir*5,beam=tips.has(n),f=flags(n.rhythm),whole=n.rhythm.denominator===1;
          const ledger:number[]=[];for(let y=layout.staffBottom+10;y<=ny;y+=10)ledger.push(y);for(let y=layout.staffBottom-50;y>=ny;y-=10)ledger.push(y);
          return <g key={i} data-time-note data-start={n.note.start} data-end={n.note.end} data-pitch={n.note.pitch} data-attack={n.attack?'true':'false'} data-segment-start={n.start} data-segment-end={n.end} data-denominator={n.rhythm.denominator}>
            <title>{`${pitchName(n.note.pitch)}: ${n.note.start.toFixed(4)}–${n.note.end.toFixed(4)}秒${layout.tuning.length?` / ${layout.tuning.length+1-n.note.string}弦 ${n.note.fret}フレット`:''}${n.attack?'':' / タイ継続'}`}</title>
            {ledger.map(y=><line key={y} x1={nx-10} x2={nx+10} y1={y} y2={y} stroke="#444"/>)}
            {n.note.sharp&&n.attack&&<text x={nx-15} y={ny+4} fontSize={14}>♯</text>}
            <ellipse data-time-onset={n.attack?n.start:undefined} data-tie-head={!n.attack?'true':undefined} cx={nx} cy={ny} rx={whole?7:5} ry={3.5} transform={`rotate(-18 ${nx} ${ny})`} fill={n.rhythm.denominator<=2?'white':'#111'} stroke="#111" strokeWidth={1.2}/>
            {!whole&&<line data-note-stem x1={stemX} x2={stemX} y1={ny} y2={tip} stroke="#111" strokeWidth={1.2}/>}
            {f>0&&!beam&&<text data-note-flag x={stemX} y={tip} className="time-music-glyph" fontFamily={layout.fontFamily} fontSize={40}>{String.fromCodePoint(57920+(f-1)*2+(dir===1?0:1))}</text>}
            {Array.from({length:n.rhythm.dots},(_,d)=><circle key={d} cx={nx+12+d*5} cy={ny-4} r={1.5}/>)}
            <Ratio r={n.rhythm} x={nx} endX={ex} y={ny-dir*45}/>
            {n.tieAfter&&<path data-note-tie d={`M${nx+6} ${ny+8} Q${(nx+ex)/2} ${ny+21} ${ex-(Math.abs(n.end-end)<1e-8?1:6)} ${ny+8}`} stroke="#111" fill="none" strokeWidth={1.1}/>}
            {n.tieBefore&&Math.abs(n.start-start)<1e-8&&<path data-note-tie d={`M${nx-14} ${ny+8} Q${nx-9} ${ny+14} ${nx-4} ${ny+8}`} stroke="#111" fill="none" strokeWidth={1.1}/>}
            {layout.tuning.length>0&&<g><rect x={nx-(n.attack?8:13)} y={layout.tabTop+(layout.tuning.length-n.note.string)*16-8} width={n.attack?16:26} height={16} fill="white"/><text data-tab-onset={n.attack?n.start:undefined} data-tab-tie={!n.attack?'true':undefined} x={nx} y={layout.tabTop+(layout.tuning.length-n.note.string)*16+4} textAnchor="middle" fontSize={13}>{n.attack?n.note.fret:`(${n.note.fret})`}</text></g>}
          </g>;
        })}
        {beams.filter(g=>g[0].row===row).map((g,i)=>{
          const dir=stemDirection(g[0]),tip=tips.get(g[0])!;
          return <g key={i} data-note-beam>{g.slice(0,-1).flatMap((a,j)=>{
            const b=g[j+1],fa=flags(a.rhythm),fb=flags(b.rhythm),ax=x(a.start)+dir*5,bx=x(b.start)+dir*5;
            return Array.from({length:Math.max(fa,fb)},(_,level)=>{
              const left=fa>level,right=fb>level;
              return <line key={`${j}-${level}`} x1={left?ax:Math.max(ax,bx-9)} x2={right?bx:Math.min(bx,ax+9)} y1={tip+dir*level*5} y2={tip+dir*level*5} stroke="#111" strokeWidth={3}/>;
            });
          })}</g>;
        })}
      </g>;
    })}
  </svg>;
}

export default memo(TimeScoreView);
