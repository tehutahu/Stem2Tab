// @vitest-environment node
import regular from "./fixtures/musical-clock/regular.musicxml?raw";
import pickup from "./fixtures/musical-clock/pickup.musicxml?raw";
import compound from "./fixtures/musical-clock/compound.musicxml?raw";
import { expect, it } from "vitest";
import { importer, midi, Settings } from "@coderline/alphatab";
import { prepareMusicalClockScore, verifyMusicalClockScore } from "../utils/musicalClockScore";

function load(name: string) {
  return importer.ScoreLoader.loadScoreFromBytes(new TextEncoder().encode(name === "pickup" ? pickup : name === "compound" ? compound : regular));
}
function performance(score: ReturnType<typeof load>) {
  const file = new midi.MidiFile();
  new midi.MidiFileGenerator(score, new Settings(), new midi.AlphaSynthMidiFileHandler(file)).generate();
  return file.events;
}
it("reproduces SDK pickup tie truncation before preparation", () => {
  const events = performance(load("pickup"));
  const release = events.find(e => e.type === midi.MidiEventType.NoteOff && "noteKey" in e && e.noteKey === 28);
  expect(release?.tick).toBe(480); // Source release is 960 ticks at 120 BPM.
});
it.each(["regular", "pickup", "compound"])("preserves exact note-on/off seconds, polyphony and tail for %s", name => {
  const score = load(name);
  prepareMusicalClockScore(score);
  const events = performance(score);
  const expected = [[28,.125,.5],[33,.5,1.25],[38,2,4.25],[40,.25,2.75]];
  const proof = { bpm: 120, horizonTicks: 8*1920, events: expected.map(([pitch,start,end]): [number,number,number] => [pitch,start*1920,end*1920]) };
  expect(() => verifyMusicalClockScore(score, proof)).not.toThrow();
  expect(() => verifyMusicalClockScore(score, { ...proof, horizonTicks: 100 })).toThrow(/一致/);
  expect(() => verifyMusicalClockScore(score, { ...proof, events: [] })).toThrow(/一致/);
  const attacks = events.filter(e => e.type === midi.MidiEventType.NoteOn);
  expect(attacks).toHaveLength(4);
  for (const [pitch,start,end] of expected) {
    const onset = events.find(e => e.type === midi.MidiEventType.NoteOn && "noteKey" in e && e.noteKey === pitch);
    const release = events.find(e => e.type === midi.MidiEventType.NoteOff && "noteKey" in e && e.noteKey === pitch);
    expect(onset?.tick).toBe(start*1920); expect(release?.tick).toBe(end*1920);
  }
  expect(events.find(e => e.type === midi.MidiEventType.EndOfTrack)?.tick).toBe(8*1920);
  const before = events;
  prepareMusicalClockScore(score);
  expect(performance(score)).toEqual(before);
  let start = 0;
  for (const bar of score.masterBars) { expect(bar.start).toBe(start); start += bar.calculateDuration(); }
});
