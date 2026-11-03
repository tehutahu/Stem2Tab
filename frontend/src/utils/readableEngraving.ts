import type { AlphaTabApi } from "@coderline/alphatab";

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object";

/** Keep PageView's normal wrapping and glyph spacing, including overfull bars.
 * Only this renderer instance is adapted. Its viewport controls line breaking;
 * the paint canvas may be wider when one bar cannot fit at readable size.
 */
export function installReadableEngravingLayout(api: Pick<AlphaTabApi, "renderer" | "settings">): void {
  const renderer: unknown = "instance" in api.renderer ? api.renderer.instance : api.renderer;
  if (!record(renderer) || !record(renderer.layout)) throw new Error("通常譜刻の配置を確認できません。");
  const layout = renderer.layout;
  const fit = layout._fitSystem;
  if (typeof fit !== "function") throw new Error("通常譜刻の折り返しに対応していません。");
  const pagePadding = (): { left: number; right: number } => {
    const padding: unknown = layout.pagePadding;
    if (!Array.isArray(padding) || typeof padding[0] !== "number" || typeof padding[2] !== "number")
      throw new Error("譜面の余白を確認できません。");
    return { left: padding[0], right: padding[2] };
  };
  const availableWidth = (): number => {
    const padding = pagePadding();
    return Math.round(api.renderer.width / api.settings.display.scale) - padding.left - padding.right;
  };
  // PageView normally derives this from its canvas width. Keep line breaks tied
  // to the viewport even after a preceding dense system expands that canvas.
  Object.defineProperty(layout, "_maxWidth", { get: availableWidth });
  layout._fitSystem = (system: unknown): void => {
    if (!record(system) || typeof system.computedWidth !== "number" || typeof system.width !== "number" ||
        typeof system.scaleToWidth !== "function" || typeof system.finalizeSystem !== "function" || typeof layout.width !== "number")
      throw new Error("通常譜刻の内部構造が変更されています。");
    if (system.computedWidth > availableWidth()) {
      // computedWidth includes the engine's original minimum glyph spacing.
      // Calling the ordinary fit here would squeeze a single overfull measure.
      system.scaleToWidth(system.computedWidth);
      system.finalizeSystem();
    } else fit.call(layout, system);
    const padding = pagePadding();
    layout.width = Math.max(layout.width, (padding.left + system.width + padding.right) * api.settings.display.scale);
  };
}
