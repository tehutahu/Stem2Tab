import { ROW_SECONDS, TIME_RATE, type TimeScore, type TimeNote } from "./timeScore";

export interface RhythmValue { denominator: number; dots: number; quarters: number; actual: number; normal: number }
export interface NotationNote {
  note: TimeNote; start: number; end: number; row: number; bar: number;
  attack: boolean; tieBefore: boolean; tieAfter: boolean; rhythm: RhythmValue;
}
export interface NotationRest { start: number; end: number; row: number; bar: number; rhythm: RhythmValue }
const EPS=1e-8;

/** Recover a bounded rational ratio; never round or replace the performed times. */
function fraction(value:number):[number,number] {
  let x=value,h0=0,h1=1,k0=1,k1=0;
  for(let i=0;i<32;i++) {
    const a=Math.floor(x),h=a*h1+h0,k=a*k1+k0;
    if(k>1e6 || !Number.isSafeInteger(h))break;
    if(Math.abs(h/k-value)<1e-10)return [h,k];
    h0=h1;h1=h;k0=k1;k1=k;x=1/(x-a);
  }
  throw new Error("原時刻の音価を正確な連符比で表せません。通常譜刻で確認してください。");
}

/** Conventional duration plus an explicit actual:normal tuplet ratio if needed. */
export function rhythmValue(quarters:number):RhythmValue {
  if(!Number.isFinite(quarters)||quarters<=0)throw new Error("音価が不正です。");
  const denominators=[1,2,4,8,16,32,64,128,256];
  for(let dots=0;dots<=2;dots++)for(const denominator of denominators) {
    const q=4/denominator*(2-1/2**dots);
    if(Math.abs(q-quarters)<EPS)return {denominator,dots,quarters,actual:1,normal:1};
  }
  const denominator=denominators.reduce((best,d)=>Math.abs(Math.log(quarters/(4/d)))<Math.abs(Math.log(quarters/(4/best)))?d:best,4);
  const [normal,actual]=fraction(quarters/(4/denominator));
  if(Math.abs(4/denominator*normal/actual-quarters)>EPS)throw new Error("連符比の検算に失敗しました。");
  return {denominator,dots:0,quarters,actual,normal};
}

export function buildTimeNotation(layout:TimeScore):{notes:NotationNote[];rests:NotationRest[];irregular:number} {
  const notes:NotationNote[]=[],rests:NotationRest[]=[];
  const cuts=[...new Set([0,layout.total,...layout.bars.flatMap(b=>[b.start,b.end]),...Array.from({length:layout.rows},(_,i)=>i*ROW_SECONDS)])].sort((a,b)=>a-b);
  function pieces(start:number,end:number) {
    const points=[start,...cuts.filter(t=>t>start+EPS&&t<end-EPS),end];
    return points.slice(0,-1).map((s,i)=>({start:s,end:points[i+1],row:Math.min(layout.rows-1,Math.floor((s+EPS)/ROW_SECONDS)),bar:layout.bars.find(b=>s>=b.start-EPS&&s<b.end-EPS)!.index,rhythm:rhythmValue((points[i+1]-s)*layout.tempo/60)}));
  }
  for(const note of layout.notes)for(const piece of pieces(note.start,note.end))notes.push({...piece,note,attack:Math.abs(piece.start-note.start)<EPS,tieBefore:piece.start>note.start+EPS,tieAfter:piece.end<note.end-EPS});
  for(const rest of layout.rests)rests.push(...pieces(rest.start,rest.end));
  return {notes,rests,irregular:[...notes,...rests].filter(n=>n.rhythm.actual!==n.rhythm.normal).length};
}

export const flags=(r:RhythmValue):number=>Math.max(0,Math.log2(r.denominator)-2);
// A short tied fragment beside a barline uses a down-stem so its flag does not
// cover the continuation head. Neither head is displaced from its exact time.
export const stemDirection=(n:NotationNote):number=>n.note.staffOffset < -20 || (n.tieAfter&&(n.end-n.start)*TIME_RATE<24) ? -1 : 1;

/** Beam contiguous regular subdivisions within one notated quarter; no timing edits. */
export function beamGroups(notes:NotationNote[],tempo:number):NotationNote[][] {
  const groups:NotationNote[][]=[];let group:NotationNote[]=[];
  const finish=()=>{if(group.length>1)groups.push(group);group=[];};
  for(const n of notes) {
    if(!flags(n.rhythm)||n.rhythm.actual!==n.rhythm.normal){finish();continue;}
    const prev=group.at(-1);
    if(prev&&(prev.row!==n.row||prev.bar!==n.bar||Math.abs(prev.end-n.start)>EPS||
      Math.floor((prev.start+EPS)*tempo/60)!==Math.floor((n.start+EPS)*tempo/60)||stemDirection(prev)!==stemDirection(n)))finish();
    group.push(n);
  }
  finish();return groups;
}
