import { model } from "@coderline/alphatab";

import { readBassTabConfig, type BassTabConfig } from "./bassTabConfig";

/** Add a view-only, all-or-none mapping using the saved low-to-high tuning.
 * This matches the existing simple highest-playable-string rule, not the optional
 * movement optimizer. Validate every note before mutation; keep ticks and pitch.
 */
export function applyBassTab(score: model.Score, config: BassTabConfig): number {
  if (!readBassTabConfig({tuning_midi:config.tuning,strings:config.tuning.length,max_fret:config.maxFret}))
    throw new Error("保存済み弦設定が不明または不正です。TAB表示を停止しました。");
  const staves = score.tracks.flatMap((track) => track.staves);
  if (staves.length !== 1 || staves[0].isPercussion || staves[0].transpositionPitch !== 0 || staves[0].capo !== 0)
    throw new Error("TAB表示は移調・カポのない単一ベース譜に限定しています。");
  const staff = staves[0];
  const mapped: Array<{ note: model.Note; pitch: number; string: number; fret: number; start: number; end: number }> = [];
  for (const bar of staff.bars) for (const voice of bar.voices) for (const beat of voice.beats) for (const note of beat.notes) {
    const pitch = note.realValue;
    let string = config.tuning.length;
    while (string > 0 && (pitch < config.tuning[string - 1] || pitch - config.tuning[string - 1] > config.maxFret)) string--;
    if (!string) throw new Error(`MIDI ${pitch} は保存弦設定の音域外です。移調・音の省略をせずTAB表示を停止しました。`);
    mapped.push({note,pitch,string,fret:pitch-config.tuning[string-1],start:beat.absolutePlaybackStart,end:beat.absolutePlaybackStart+beat.playbackDuration});
  }
  const sorted = [...mapped].sort((a,b)=>a.start-b.start || a.end-b.end);
  const ends = new Map<number,number>();
  for (const n of sorted) {
    if ((ends.get(n.string) ?? -Infinity) > n.start + 1e-6)
      throw new Error("同じ弦に重なる音があります。音を消さずTAB表示を停止しました。運指別版で割当を確認してください。");
    ends.set(n.string,n.end);
  }
  staff.stringTuning = new model.Tuning("保存済みベース設定", [...config.tuning].reverse(), false);
  staff.showTablature = true; staff.showStandardNotation = true; staff.displayTranspositionPitch = 0;
  for (const bar of staff.bars) bar.clef = model.Clef.F4;
  for (const n of mapped) { n.note.string=n.string; n.note.fret=n.fret; }
  if (mapped.some((n)=>n.note.realValue!==n.pitch)) throw new Error("TAB割当による音高の不一致を検出しました。");
  return mapped.filter((n)=>!n.note.isTieDestination).length;
}
