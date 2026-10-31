import type { AlphaTabApi } from "@coderline/alphatab";
import { buildTimeScore } from "./timeScore";
import { buildCursorBars, type CursorBar } from "./scoreCursor";

export const LIBRARY_TIME_RATE = 360;
const HEADER = 100;

/** Local adapter for the installed alphaTab page renderer. No shared prototypes,
 * glyphs, score events or dependency files are modified. Unsupported structures
 * fail closed rather than falling back to a misleading synchronization view.
 * These internal layout hooks require the integration test on SDK updates.
 */
export function installLibraryTimeLayout(api: AlphaTabApi): void {
  // alphaTab exposes its renderer; its layout internals are isolated here.
  const renderer = (api.renderer as any).instance ?? api.renderer;
  const layout = renderer.layout;
  if (!layout || typeof layout._fitSystem !== "function") throw new Error("この譜刻エンジンの時間比例配置には対応していません。通常譜刻へ戻してください。");
  layout._fitSystem = (system: any): void => {
    const score = renderer.score;
    buildTimeScore(score, false); // fixed clock, no repeats/tempo changes
    const columns = system.masterBarsRenderers;
    if (columns.length !== 1 || !Array.isArray(system._allStaves)) throw new Error("時間比例譜刻は1小節ずつの表示に限定しています。");
    const column = columns[0], master = column.masterBar;
    const secondsPerTick = 60 / score.tempo / 960;
    const duration = master.calculateDuration() * secondsPerTick;
    const width = HEADER + duration * LIBRARY_TIME_RATE + 14;
    const positions = new Map<number, number>();
    for (const r of column.renderers) {
      if (!r._preBeatGlyphs || !r._postBeatGlyphs || typeof r.scaleToWidth !== "function") throw new Error("譜刻の内部構造が変更されています。");
      for (const voice of r.bar.voices) for (const beat of voice.beats) {
        if (beat.graceType) throw new Error("装飾音の時間比例譜刻は未対応です。");
        const target = HEADER + beat.playbackStart * secondsPerTick * LIBRARY_TIME_RATE;
        const previous = positions.get(beat.absoluteDisplayStart);
        if (previous !== undefined && Math.abs(previous - target) > 0.001) throw new Error("同時声部の表示時刻が一致しません。");
        positions.set(beat.absoluteDisplayStart, target);
      }
    }
    for (const staff of system._allStaves) {
      staff._sharedLayoutData = new Map();
      for (const r of staff.barRenderers) {
        r.x = 0; r.y = staff.topSpacing + staff.topOverflow;
        // Keep all symbols drawn by alphaTab, including clef, barlines, ties,
        // beams, rests and TAB. Only on-time column positions are replaced.
        const pre = r._preBeatGlyphs.width;
        r.layoutingInfo.buildOnTimePositions = () => new Map([...positions].map(([tick, x]) => [tick, x - pre]));
        r.layoutingInfo.calculateVoiceWidth = () => width - pre - r._postBeatGlyphs.width;
        r.scaleToWidth(width);
      }
    }
    system.width = width + system.accoladeWidth;
    system.finalizeSystem();
  };
}

/** The library's measured note columns establish the actual staff origin. */
export function buildLibraryTimeCursor(api: AlphaTabApi, tempo: number): CursorBar[] {
  const rate = LIBRARY_TIME_RATE * 60 / tempo / 960;
  return buildCursorBars(api).map(bar => {
    const source = bar.points.filter(p => p.tick < bar.end);
    const first = source[0];
    const origin = first.x - (first.tick - bar.start) * rate;
    for (const point of source) if (Math.abs(point.x - (origin + (point.tick - bar.start) * rate)) > 0.05)
      throw new Error("譜刻音符と時間比例座標が一致しません。同期表示を停止しました。");
    const period = api.tickCache!.masterBars.find(p => p.masterBar.index === bar.index)!;
    const bounds = api.renderer.boundsLookup!.findMasterBar(period.masterBar)!;
    for (const staff of bounds.bars) for (const beat of staff.beats)
      if (Math.abs(beat.onNotesX - (origin + beat.beat.playbackStart * rate)) > 0.05)
        throw new Error("五線譜とTABの発音位置が一致しません。同期表示を停止しました。");
    return { ...bar, points: [{ tick: bar.start, x: origin }, { tick: bar.end, x: origin + (bar.end-bar.start)*rate }] };
  });
}
