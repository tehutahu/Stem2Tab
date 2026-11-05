// @vitest-environment node
import { expect, it } from "vitest";
import { importer, midi, rendering, Settings } from "@coderline/alphatab";
import { installReadableEngravingLayout } from "../utils/readableEngraving";
import { buildCursorMap, cursorAtTick } from "../utils/scoreCursor";
import { applyBassTab } from "../utils/scoreTab";

function syntheticScore(counts: number[] = [64, 64, 64, 64]) {
  const xml = `<score-partwise version="4.0"><part-list><score-part id="P"><part-name>Synthetic</part-name></score-part></part-list><part id="P">${counts.map((slots, bar) => `<measure number="${bar + 1}">${bar === 0 ? '<attributes><divisions>960</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>F</sign><line>4</line></clef></attributes><direction><sound tempo="60"/></direction>' : ''}${Array.from({ length: slots }, (_, i) => `<note>${i === 2 ? '<rest/>' : `<pitch><step>${bar === 0 && i === slots - 1 ? 'E' : ['E', 'F', 'G', 'A'][i % 4]}</step><octave>2</octave></pitch>`}<duration>${3840 / slots}</duration><type>${slots === 64 ? '64th' : slots === 4 ? 'quarter' : 'eighth'}</type>${bar === 0 && i === slots - 1 ? '<tie type="start"/><notations><tied type="start"/></notations>' : bar === 1 && i === 0 ? '<tie type="stop"/><notations><tied type="stop"/></notations>' : ''}</note>`).join('')}</measure>`).join('')}</part></score-partwise>`;
  return importer.ScoreLoader.loadScoreFromBytes(new TextEncoder().encode(xml));
}

function setup({ counts, width = 324, scale = 1, tab = true, adapt = true }: { counts?: number[]; width?: number; scale?: number; tab?: boolean; adapt?: boolean } = {}) {
  const score = syntheticScore(counts);
  if (tab) applyBassTab(score, { tuning: [28, 33, 38, 43], maxFret: 24 });
  const settings = new Settings();
  settings.core.engine = "svg";
  settings.core.enableLazyLoading = false;
  settings.display.padding = [12, 20, 12, 20];
  settings.display.scale = scale;
  const renderer = new rendering.ScoreRenderer(settings);
  renderer.width = width;
  const generate = () => {
    const file = new midi.MidiFile();
    const generator = new midi.MidiFileGenerator(score, settings, new midi.AlphaSynthMidiFileHandler(file));
    generator.generate();
    return { events: file.events, ticks: generator.tickLookup };
  };
  const before = generate();
  const api = { renderer, settings, tickCache: before.ticks };
  if (adapt) installReadableEngravingLayout(api);
  const painted: Array<{ width: number; right: number }> = [];
  renderer.partialRenderFinished.on(result => {
    for (let i = result.firstMasterBarIndex; i <= result.lastMasterBarIndex; i++) {
      const bar = score.masterBars[i];
      if (!bar) continue;
      const bounds = renderer.boundsLookup!.findMasterBar(bar);
      if (bounds) painted.push({ width: result.width, right: (bounds.visualBounds.x + bounds.visualBounds.w) * settings.display.scale });
    }
  });
  renderer.renderScore(score, [0]);
  const gaps = (index = 0) => {
    const bar = renderer.boundsLookup!.findMasterBar(score.masterBars[index])!;
    const columns = bar.bars[0].beats.map(beat => beat.onNotesX);
    return columns.slice(1).map((x, i) => x - columns[i]);
  };
  return { api, score, renderer, settings, generate, before, painted, gaps };
}

it.each([false, true])("keeps dense normal columns readable across sizes and resizing, TAB=%s", tab => {
  const { api, renderer, settings, score, generate, before, gaps, painted } = setup({ tab });
  for (const scale of [0.8, 1, 1.2]) {
    settings.display.scale = scale;
    renderer.render();
    for (const width of [254, 324, 702, 1214]) {
      renderer.width = width;
      renderer.resizeRender();
      expect(Math.min(...gaps())).toBeGreaterThanOrEqual(12 * scale);
      const map = buildCursorMap(api);
      expect(map.unavailableBars).toEqual([]);
      for (const master of score.masterBars) {
        const bar = renderer.boundsLookup!.findMasterBar(master)!;
        for (const staff of bar.bars) for (const beat of staff.beats) {
          expect(cursorAtTick(map.bars, master.start + beat.beat.playbackStart)!.x).toBeCloseTo(beat.onNotesX, 5);
        }
      }
      expect(generate().events).toEqual(before.events);
    }
  }
  expect(painted.length).toBeGreaterThan(0);
  for (const item of painted) expect(item.width + 0.01).toBeGreaterThanOrEqual(item.right);
});

it("uses the viewport to wrap sparse bars following an overfull measure, then fits again after widening", () => {
  const { score, renderer } = setup({ counts: [64, 4, 4, 4], width: 324, tab: false });
  const bounds = (index: number) => renderer.boundsLookup!.findMasterBar(score.masterBars[index])!.visualBounds;
  expect(bounds(0).x + bounds(0).w).toBeGreaterThan(324);
  for (let i = 1; i < score.masterBars.length; i++) expect(bounds(i).x + bounds(i).w).toBeLessThanOrEqual(324);
  expect(bounds(2).y).toBeGreaterThan(bounds(1).y);
  renderer.width = 4096;
  renderer.resizeRender();
  for (let i = 0; i < score.masterBars.length; i++) expect(bounds(i).x + bounds(i).w).toBeLessThanOrEqual(4096);
});

it("adapts only the selected renderer instance", () => {
  const adapted = setup(), untouched = setup({ adapt: false });
  expect(Math.min(...adapted.gaps())).toBeGreaterThanOrEqual(12);
  expect(Math.min(...untouched.gaps())).toBeLessThan(4);
});
