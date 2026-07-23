// AlphaTabにはMusicXML系のみを渡す（GP系はダウンロード用に残す）
const ALPHATAB_DISPLAY_EXTENSIONS = [".musicxml", ".mxl", ".xml"];
const ALPHATAB_DOWNLOAD_EXTENSIONS = [".gp5", ".gpx", ".gp4", ".gp3"];
const ALPHATAB_SUPPORTED_EXTENSIONS = [...ALPHATAB_DISPLAY_EXTENSIONS, ...ALPHATAB_DOWNLOAD_EXTENSIONS];

export const ALPHATAB_SUPPORTED_LABEL = "MusicXML (表示用), GP3/GP4/GP5/GPX (ダウンロード用)";

export function pickAlphaTabScore(files: string[]): string | undefined {
  for (const ext of ALPHATAB_DISPLAY_EXTENSIONS) {
    const match = files.find((name) => name.toLowerCase().endsWith(ext));
    if (match) return match;
  }
  return undefined;
}

export function listAlphaTabScores(files: string[]): string[] {
  const lower = files.map((f) => f.toLowerCase());
  const picked = new Set<string>();
  for (const ext of ALPHATAB_DISPLAY_EXTENSIONS) {
    lower.forEach((name, idx) => {
      if (!picked.has(files[idx]) && name.endsWith(ext)) {
        picked.add(files[idx]);
      }
    });
  }
  return Array.from(picked);
}

