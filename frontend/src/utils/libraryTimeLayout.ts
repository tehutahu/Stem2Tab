import type { AlphaTabApi } from "@coderline/alphatab";
import { buildTimeScore } from "./timeScore";
import { buildCursorBars, type CursorBar } from "./scoreCursor";

/** Rates are recorded in rendered CSS pixels per tick, one per measure. */
export interface LibraryTimeGeometry { rates: Map<number, number> }
const HEADER = 100;
type CursorApi = Pick<AlphaTabApi, "renderer" | "tickCache">;

/** Local adapter for the installed alphaTab page renderer. No shared prototypes,
 * glyphs, score events or dependency files are modified. Unsupported structures
 * fail closed rather than falling back to a misleading synchronization view.
 * These internal layout hooks require the integration test on SDK updates.
 */
export function installLibraryTimeLayout(api: Pick<AlphaTabApi, "renderer">): LibraryTimeGeometry {
  const geometry: LibraryTimeGeometry = { rates: new Map() };
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
    const scale = renderer.settings.display.scale;
    const padding = layout.pagePadding;
    const available = renderer.width / scale - padding[0] - padding[2] - system.accoladeWidth;
    const columnsByTime = new Map<number, { before: number; after: number }>();
    let header = HEADER, tail = 14;
    for (const r of column.renderers) {
      if (!r._preBeatGlyphs || !r._postBeatGlyphs || typeof r.layoutingInfo.getPreBeatSize !== "function" || typeof r.scaleToWidth !== "function") throw new Error("譜刻の内部構造が変更されています。");
      header = Math.max(header, r._preBeatGlyphs.width + 24);
      tail = Math.max(tail, r._postBeatGlyphs.width + 6);
      for (const voice of r.bar.voices) for (const beat of voice.beats) {
        const time = beat.playbackStart * secondsPerTick;
        const existing = columnsByTime.get(time);
        columnsByTime.set(time, {
          before: Math.max(existing?.before ?? 0, r.layoutingInfo.getPreBeatSize(beat)),
          after: Math.max(existing?.after ?? 0, r.layoutingInfo.getPostBeatSize(beat)),
        });
      }
    }
    // Reserve the engine's measured accidental/note/rest/TAB widths. A dense
    // measure can scroll rather than compressing two distinct attacks together.
    const times = [...columnsByTime.keys()].sort((a, b) => a - b);
    let minimumRate = 0;
    for (let i = 1; i < times.length; i++) {
      const previous = columnsByTime.get(times[i - 1])!;
      const next = columnsByTime.get(times[i])!;
      minimumRate = Math.max(minimumRate, (previous.after + next.before + 6) / (times[i] - times[i - 1]));
    }
    const lastTime = times.at(-1);
    if (lastTime !== undefined && duration > lastTime) minimumRate = Math.max(minimumRate, (columnsByTime.get(lastTime)!.after + 6) / (duration - lastTime));
    const rate = Math.max(minimumRate, (available - header - tail) / duration);
    const width = header + duration * rate + tail;
    geometry.rates.set(master.index, rate * secondsPerTick * scale);
    // PageView otherwise paints a viewport-sized SVG even for a wider system.
    // Expand its canvas, while renderer.width remains the measured viewport.
    layout.width = Math.max(layout.width, (padding[0] + system.accoladeWidth + width + padding[2]) * scale);
    const positions = new Map<number, number>();
    for (const r of column.renderers) {
      if (!r._preBeatGlyphs || !r._postBeatGlyphs || typeof r.scaleToWidth !== "function") throw new Error("譜刻の内部構造が変更されています。");
      for (const voice of r.bar.voices) for (const beat of voice.beats) {
        if (beat.graceType) throw new Error("装飾音の時間比例譜刻は未対応です。");
        const target = header + beat.playbackStart * secondsPerTick * rate;
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
  return geometry;
}

/** The library's measured note columns establish the actual staff origin. */
export function buildLibraryTimeCursor(api: CursorApi, geometry: LibraryTimeGeometry): CursorBar[] {
  return buildCursorBars(api).map(bar => {
    const rate = geometry.rates.get(bar.index);
    if (rate === undefined) throw new Error("時間比例配置の縮尺を確認できません。");
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
