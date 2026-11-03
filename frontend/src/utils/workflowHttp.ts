/** Validate the HTTP boundary before any workflow state is interpreted. */
export class WorkflowHttpError extends Error {
  constructor(public readonly status: number, public readonly detail: string | null = null) {
    const fallback = status === 422
      ? "入力の検証で拒否されました。音源の形式と処理範囲を確認してください。"
      : "サーバーから詳しい理由を取得できませんでした。";
    super(`API応答 HTTP ${status}。${detail || fallback}`);
  }
}

const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);
const fields = new Map([
  ["file", "音源"], ["request_id", "処理ID"], ["rights_confirmed", "使用権の確認"],
  ["start_seconds", "開始秒"], ["end_seconds", "終了秒"],
  ["profile", "弦構成"], ["max_fret", "最大フレット"],
]);
const clean = (text: string): string => text.replace(/[\u0000-\u001f\u007f]/g, " ").slice(0, 500).trim();

/** Read public error messages, excluding validation input values and context. */
function errorDetail(text: string, type: string): string | null {
  if (type !== "application/json" && !type.endsWith("+json")) return null;
  let value: unknown;
  try { value = JSON.parse(text); } catch { return null; }
  if (!record(value)) return null;
  const detail = value.detail;
  if (typeof detail === "string") return clean(detail) || null;
  if (record(detail) && typeof detail.message === "string") {
    const code = typeof detail.code === "string" && /^[a-z0-9_]{1,64}$/.test(detail.code) ? ` [${detail.code}]` : "";
    return clean(detail.message) ? `${clean(detail.message)}${code}` : null;
  }
  if (!Array.isArray(detail)) return null;
  const messages: string[] = [];
  for (const item of detail.slice(0, 5)) {
    if (!record(item) || typeof item.msg !== "string") continue;
    const label = Array.isArray(item.loc)
      ? item.loc.filter((part): part is string => typeof part === "string").map(part => fields.get(part)).find(Boolean)
      : undefined;
    const message = clean(item.msg);
    if (message) messages.push(`${label || "入力"}: ${message}`);
  }
  return messages.join(" / ") || null;
}

export async function workflowJson(url: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(url, { cache: "no-store", ...init });
  init.signal?.throwIfAborted();
  const type = (response.headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase();
  if (!response.ok) {
    // POST 5xx has an unknown outcome: callers retain the original request ID.
    let detail: string | null = null;
    try { detail = errorDetail(await response.text(), type); }
    catch { /* The received HTTP rejection remains valid if its body is incomplete. */ }
    init.signal?.throwIfAborted();
    throw new WorkflowHttpError(response.status, detail);
  }
  const text = await response.text();
  init.signal?.throwIfAborted();
  if (!text.trim() || (type !== "application/json" && !type.endsWith("+json"))) {
    throw new Error("APIの応答が空かJSON形式ではありません。処理結果を確認できません。");
  }
  try { return JSON.parse(text); }
  catch { throw new Error("APIの応答が途中で切れているか壊れています。処理結果を確認できません。"); }
}
