import type { model } from "@coderline/alphatab";
import type { CursorPosition } from "./scoreCursor";

export const TIME_LEFT = 80, TIME_WIDTH = 720, ROW_SECONDS = 4, TIME_RATE = TIME_WIDTH / ROW_SECONDS;
export interface TimeNote { pitch: number; start: number; end: number; string: number; fret: number; staffOffset: number; sharp: boolean }
export interface TimeScore {
  fontFamily?: string;
  tempo: number; total: number; notes: TimeNote[]; tuning: number[];
  bars: Array<{ index: number; start: number; end: number }>;
  rests: Array<{ start: number; end: number }>;
  rows: number; rowHeight: number; staffBottom: number; tabTop: number; contentBottom: number;
}
const EPS = 1e-8;

/** Sounding bass-clef coordinates, spelling chromatic notes with sharps. */
export function staffPitch(pitch: number): { staffOffset: number; sharp: boolean } {
  const steps = [0,0,1,1,2,3,3,4,4,5,5,6];
  const diatonic = (Math.floor(pitch / 12) - 1) * 7 + steps[pitch % 12];
  return { staffOffset: (18 - diatonic) * 5, sharp: [1,3,6,8,10].includes(pitch % 12) };
}

/** Read the loaded score without changing pitches, playback data or engraving.
 * This bounded synchronization view supports one fixed-clock bass staff. Ties
 * become a single duration bar; only real attacks receive noteheads/fret labels.
 */
export function buildTimeScore(score: model.Score, showTab: boolean): TimeScore {
  const staff = score.tracks[0]?.staves[0], tempo = score.tempo;
  if (score.tracks.length !== 1 || score.tracks[0].staves.length !== 1 || !staff || staff.isPercussion ||
      staff.transpositionPitch !== 0 || !Number.isFinite(tempo) || tempo <= 0)
    throw new Error("時間比例表示は固定テンポの単一ベース譜のみ対応しています。通常譜刻へ戻してください。");
  if (score.masterBars.some(b => b.isRepeatStart || b.repeatCount > 0 || b.alternateEndings || b.isAnacrusis ||
      b.timeSignatureNumerator !== 4 || b.timeSignatureDenominator !== 4 ||
      b.tempoAutomations.some(a => Math.abs(a.value - tempo) > EPS)))
    throw new Error("時間比例表示は固定4/4・反復なしの保存クロックに限定しています。原版は変更していません。");
  const seconds = (tick: number): number => tick / 960 * 60 / tempo;
  const bars = score.masterBars.map(b => ({ index: b.index, start: seconds(b.start), end: seconds(b.start+b.calculateDuration()) }));
  const total = bars.at(-1)?.end ?? 0;
  if (!Number.isFinite(total) || total <= 0 || total > 1200) throw new Error("時間比例表示の時間範囲を確認できません。");
  const grouped = new Map<model.Note, TimeNote>();
  for (const bar of staff.bars) for (const voice of bar.voices) for (const beat of voice.beats) for (const note of beat.notes) {
    const start=seconds(beat.absolutePlaybackStart), end=seconds(beat.absolutePlaybackStart+beat.playbackDuration);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < -EPS || end <= start || end > total+EPS)
      throw new Error("発音時刻が不正なため時間比例表示を停止しました。");
    if (note.isTieDestination) {
      let origin=note.tieOrigin; const seen=new Set<model.Note>();
      while (origin?.isTieDestination && !seen.has(origin)) { seen.add(origin); origin=origin.tieOrigin; }
      const previous=origin ? grouped.get(origin) : undefined;
      if (!previous || previous.pitch !== note.realValue || Math.abs(previous.end-start)>EPS)
        throw new Error("タイの連結を確認できません。時間比例表示を停止しました。");
      previous.end=end;
    } else {
      if (showTab && (note.string<1 || note.string>staff.tuning.length || note.fret<0 ||
          staff.tuning[staff.tuning.length-note.string]+note.fret !== note.realValue))
        throw new Error("TABの実音高と弦・フレットが一致しません。");
      grouped.set(note,{pitch:note.realValue,start,end,string:note.string,fret:note.fret,...staffPitch(note.realValue)});
    }
  }
  const notes=[...grouped.values()].sort((a,b)=>a.start-b.start||a.pitch-b.pitch);
  if (notes.length>2000) throw new Error("時間比例表示は2000発音まで対応しています。");
  const rests: TimeScore["rests"]=[]; let end=0;
  for(const n of notes){if(n.start>end+EPS)rests.push({start:end,end:n.start});end=Math.max(end,n.end);}
  if(end<total-EPS)rests.push({start:end,end:total});
  const upper=Math.min(-40,...notes.map(n=>n.staffOffset)),lower=Math.max(0,...notes.map(n=>n.staffOffset));
  // Extra vertical lane for exact tuplet ratios/rests; the horizontal clock stays fixed.
  const staffBottom=80-upper,tabTop=staffBottom+lower+42;
  const tuning=showTab?[...staff.tuning]:[];
  const contentBottom=tuning.length?tabTop+(tuning.length-1)*16+12:staffBottom+lower+14;
  return {tempo,total,notes,bars,rests,tuning,staffBottom,tabTop,contentBottom,rowHeight:contentBottom+38,rows:Math.ceil((total-EPS)/ROW_SECONDS)};
}

export function timeX(seconds: number, row: number): number { return TIME_LEFT+(seconds-row*ROW_SECONDS)*TIME_RATE; }

/** The same affine mapping positions both attack glyphs and the cursor. */
export function timeCursor(layout: TimeScore, tick: number): CursorPosition | null {
  if(!Number.isFinite(tick))return null;
  const seconds=Math.max(0,Math.min(layout.total,tick/960*60/layout.tempo));
  const row=Math.min(layout.rows-1,Math.floor(seconds/ROW_SECONDS));
  const bar=layout.bars.find(b=>seconds>=b.start-EPS&&seconds<b.end-EPS)??layout.bars[layout.bars.length-1];
  return {bar:bar.index,tick,x:timeX(seconds,row),y:row*layout.rowHeight+32,height:layout.contentBottom-24};
}
