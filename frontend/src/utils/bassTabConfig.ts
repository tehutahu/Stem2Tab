export interface BassTabConfig { tuning: number[]; maxFret: number }

export function readBassTabConfig(config: Record<string, unknown>): BassTabConfig | null {
  const t = config.tuning_midi, max = config.max_fret;
  if (!Array.isArray(t) || t.length !== config.strings || t.length < 1 || t.length > 7 ||
      t.some((v, i) => !Number.isInteger(v) || v < 0 || v > 127 || (i > 0 && v <= t[i - 1])) ||
      typeof max !== "number" || !Number.isInteger(max) || max < 0 || max > 36) return null;
  return { tuning: [...t], maxFret: max };
}
