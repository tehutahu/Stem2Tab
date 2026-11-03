// @vitest-environment node
import { expect, it } from "vitest";
import { importer, midi, rendering, Settings } from "@coderline/alphatab";
import { buildLibraryTimeCursor, installLibraryTimeLayout } from "../utils/libraryTimeLayout";
import { cursorAtTick } from "../utils/scoreCursor";
import { applyBassTab } from "../utils/scoreTab";

function syntheticScore(dense = false) {
  const note = (duration: number, tie = "") => `<note><pitch><step>E</step><octave>1</octave></pitch><duration>${duration}</duration><voice>1</voice>${tie ? `<notations><tied type="${tie}"/></notations>` : ""}</note>`;
  return importer.ScoreLoader.loadScoreFromBytes(new TextEncoder().encode(`<score-partwise version="4.0"><part-list><score-part id="P"><part-name>Synthetic</part-name></score-part></part-list><part id="P"><measure number="1"><attributes><divisions>100</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>F</sign><line>4</line></clef></attributes><direction><sound tempo="60"/></direction>${dense ? note(1) + note(1) + note(298) : note(100) + '<note><rest/><duration>100</duration><voice>1</voice></note>' + note(100)}${note(100, "start")}</measure><measure number="2">${note(100, "stop")}<note><rest/><duration>300</duration><voice>1</voice></note></measure></part></score-partwise>`));
}
function performance(score: ReturnType<typeof syntheticScore>) {
  const file = new midi.MidiFile();
  new midi.MidiFileGenerator(score, new Settings(), new midi.AlphaSynthMidiFileHandler(file)).generate();
  return file.events;
}
function setup({ width, scale = 1, dense = false, tab = false }: { width: number; scale?: number; dense?: boolean; tab?: boolean }) {
  const score = syntheticScore(dense);
  if (tab) applyBassTab(score, { tuning: [28, 33, 38, 43], maxFret: 24 });
  const before = performance(score);
  const settings = new Settings();
  settings.core.engine = "svg";
  settings.core.enableLazyLoading = false;
  settings.display.barsPerRow = 1;
  settings.display.scale = scale;
  settings.display.padding = [12, 20, 12, 20];
  const renderer = new rendering.ScoreRenderer(settings);
  renderer.width = width;
  const generator = new midi.MidiFileGenerator(score, settings, new midi.AlphaSynthMidiFileHandler(new midi.MidiFile()));
  generator.generate();
  const api = { renderer, tickCache: generator.tickLookup };
  const geometry = installLibraryTimeLayout(api);
  const painted: Array<{ width: number; right: number }> = [];
  renderer.partialRenderFinished.on(result => {
    const bar = score.masterBars[result.firstMasterBarIndex];
    if (!bar) return;
    const bounds = renderer.boundsLookup!.findMasterBar(bar)!;
    painted.push({ width: result.width * settings.display.scale, right: (bounds.visualBounds.x + bounds.visualBounds.w) * settings.display.scale });
  });
  renderer.renderScore(score, [0]);
  return { api, geometry, score, before, painted, renderer, settings };
}

it.each([254, 320, 375, 768, 1024])("fits a sparse four-second bar into %ipx using actual renderer geometry", width => {
  const { score, renderer } = setup({ width });
  for (const bar of score.masterBars) {
    const bounds = renderer.boundsLookup!.findMasterBar(bar)!;
    expect(bounds.visualBounds.x + bounds.visualBounds.w).toBeLessThanOrEqual(width);
  }
});

it.each([0.8, 1, 1.2])("preserves performance and aligns actual notes, TAB and cursor after resize at scale %s", scale => {
  const { api, geometry, score, before, renderer } = setup({ width: 768, scale, tab: true });
  const originalRate = geometry.rates.get(0)!;
  for (const width of [375, 1024, 320, 768]) {
    renderer.width = width;
    renderer.resizeRender();
    const paths = buildLibraryTimeCursor(api, geometry);
    expect(paths).toHaveLength(2);
    for (const bar of score.masterBars) {
      const bounds = renderer.boundsLookup!.findMasterBar(bar)!;
      for (const staff of bounds.bars) for (const beat of staff.beats) {
        expect(cursorAtTick(paths, bar.start + beat.beat.playbackStart)!.x).toBeCloseTo(beat.onNotesX, 5);
      }
      const first = cursorAtTick(paths, bar.start)!;
      const second = cursorAtTick(paths, bar.start + 240)!;
      const third = cursorAtTick(paths, bar.start + 480)!;
      expect(second.x - first.x).toBeCloseTo(third.x - second.x, 7);
    }
    expect(performance(score)).toEqual(before);
  }
  expect(geometry.rates.get(0)).toBeCloseTo(originalRate, 8);
});

it("keeps dense attacks distinct and paints the whole wide system without cutting off its ending", () => {
  const { api, geometry, score, before, painted, renderer } = setup({ width: 375, dense: true, tab: true });
  const paths = buildLibraryTimeCursor(api, geometry);
  const x = (tick: number) => cursorAtTick(paths, tick)!.x;
  expect(x(9) - x(0)).toBeGreaterThan(10);
  expect(paths[0].points.at(-1)!.x).toBeGreaterThan(375);
  expect(painted.length).toBeGreaterThan(0);
  for (const item of painted) expect(item.width).toBeGreaterThanOrEqual(item.right);
  expect(performance(score)).toEqual(before);
  renderer.width = 1024;
  renderer.resizeRender();
  expect(() => buildLibraryTimeCursor(api, geometry)).not.toThrow();
});
