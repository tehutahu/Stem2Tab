import { midi, Settings, type model } from "@coderline/alphatab";
import type { MusicalClockProof } from "./musicalClock";

/** Restore source-zero timing after importing explicit partial MusicXML measures.
 * alphaTab's importer omits pickup lengths from masterBar.start. Its MIDI tie
 * calculation then shortens a note crossing the pickup boundary. Once voices
 * are finished, actual partial-bar durations are known and starts can be set.
 * Only the explicit musical-clock derivative uses this correction.
 */
export function prepareMusicalClockScore(score: model.Score): void {
  let start = 0;
  for (const bar of score.masterBars) {
    const duration = bar.calculateDuration();
    if (!Number.isSafeInteger(duration) || duration <= 0 || bar.isRepeatStart || bar.repeatCount > 0 || bar.alternateEndings) {
      throw new Error("音楽的クロック別版の小節時刻を確認できません。正確なMusicXMLを保存して確認してください。");
    }
    bar.start = start;
    start += duration;
  }
}

/** Verify the actual renderer-generated note attacks/releases before playback. */
export function verifyMusicalClockScore(score: model.Score, proof: MusicalClockProof): void {
  const file = new midi.MidiFile();
  new midi.MidiFileGenerator(score, new Settings(), new midi.AlphaSynthMidiFileHandler(file)).generate();
  const actual: Array<[number, number, number]> = [];
  for (const event of file.events) {
    if ((event.type === midi.MidiEventType.NoteOn || event.type === midi.MidiEventType.NoteOff) && "noteKey" in event && typeof event.noteKey === "number") {
      actual.push([event.type, event.noteKey, event.tick]);
    }
  }
  const expected = proof.events.flatMap(([pitch, start, end]) => [[midi.MidiEventType.NoteOn, pitch, start], [midi.MidiEventType.NoteOff, pitch, end]]);
  const order = (a: number[], b: number[]): number => a[2]-b[2] || a[0]-b[0] || a[1]-b[1];
  const tail = Math.max(...file.events.filter(e => e.type === midi.MidiEventType.EndOfTrack).map(e => e.tick));
  if (score.tempo !== proof.bpm || tail !== proof.horizonTicks || JSON.stringify(actual.sort(order)) !== JSON.stringify(expected.sort(order))) {
    throw new Error("ブラウザの再生時刻が原MIDIの厳密な時刻と一致しないため、この別版のプレビューを停止しました。正確なMusicXMLと設定レポートを保存できます。");
  }
}
