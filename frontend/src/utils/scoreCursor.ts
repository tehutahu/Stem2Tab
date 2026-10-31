import type { AlphaTabApi } from "@coderline/alphatab";

export interface CursorBar {
  index: number; start: number; end: number; y: number; height: number;
  points: Array<{ tick: number; x: number }>;
}
export interface CursorPosition { bar: number; tick: number; x: number; y: number; height: number }
const EPSILON = 1e-6;

/** Build one time-ordered path per rendered bar, including rests/ties/all voices.
 * Use the public playback schedule (also handles repeats), never note-on events.
 * Coordinates are rebuilt after every layout; the path has no animation history.
 */
export function buildCursorBars(api: Pick<AlphaTabApi, "tickCache" | "renderer">): CursorBar[] {
  const lookup = api.renderer.boundsLookup;
  if (!lookup || !api.tickCache) return [];
  return api.tickCache.masterBars.flatMap((period) => {
    const bounds = lookup.findMasterBar(period.masterBar);
    if (!bounds) return [];
    const raw = bounds.bars.flatMap((bar) => bar.beats.map((b) => ({
      tick: period.start + b.beat.playbackStart, x: b.onNotesX,
    }))).sort((a, b) => a.tick - b.tick || a.x - b.x);
    if (!raw.length) return [];
    const points: CursorBar["points"] = [];
    for (const point of raw) {
      const previous = points[points.length - 1];
      if (![point.tick, point.x].every(Number.isFinite) || point.tick < period.start - EPSILON || point.tick >= period.end + EPSILON)
        throw new Error("譜面の時刻と小節範囲が一致しないため、カーソルを停止しました。");
      // Simultaneous voices may place glyphs slightly apart; use the first column.
      if (previous && Math.abs(point.tick - previous.tick) < EPSILON) continue;
      if (previous && point.x < previous.x - EPSILON)
        throw new Error("譜面座標が同一小節内で逆行しています。誤った位置を表示できません。");
      points.push(point);
    }
    if (points[0].tick > period.start) points.unshift({ tick: period.start, x: points[0].x });
    const right = bounds.visualBounds.x + bounds.visualBounds.w;
    if (right < points[points.length - 1].x || period.end <= period.start)
      throw new Error("譜面の小節境界を確認できません。");
    points.push({ tick: period.end, x: right });
    return [{ index: period.masterBar.index, start: period.start, end: period.end,
      y: bounds.visualBounds.y, height: bounds.visualBounds.h, points }];
  });
}

/** Interpolate only between the surrounding columns; never extrapolate past them. */
export function cursorAtTick(bars: CursorBar[], tick: number): CursorPosition | null {
  if (!Number.isFinite(tick) || !bars.length) return null;
  const bounded = Math.max(bars[0].start, Math.min(tick, bars[bars.length - 1].end));
  const bar = bars.find((b) => bounded >= b.start && bounded < b.end) ?? bars[bars.length - 1];
  let upper = bar.points.findIndex((p) => p.tick > bounded);
  if (upper < 0) upper = bar.points.length - 1;
  const a = bar.points[Math.max(0, upper - 1)], b = bar.points[upper];
  const ratio = b.tick > a.tick ? Math.max(0, Math.min(1, (bounded - a.tick) / (b.tick - a.tick))) : 0;
  return { bar: bar.index, tick: bounded, x: a.x + (b.x - a.x) * ratio, y: bar.y, height: bar.height };
}
