/** Diagnose the local HTTP boundary before interpreting JSON as review metadata. */
export interface ReviewResponseDiagnostic {
  url: string;
  stage: "fetch" | "read" | "http" | "empty" | "type" | "parse";
  status: number | null;
  contentType: string | null;
  bodyBytes: number | null;
  declaredContentLength: string | null;
}

export class ReviewResponseError extends Error {
  constructor(message: string, public readonly diagnostic: ReviewResponseDiagnostic) {
    super(message);
    this.name = "ReviewResponseError";
  }
}

/** Reject failed, empty, wrong-type or truncated responses; never substitute data. */
export async function readReviewJson(url: string, label: string, signal?: AbortSignal): Promise<unknown> {
  signal?.throwIfAborted();
  let response: Response;
  const fail = (diagnostic: ReviewResponseDiagnostic, reason: string): never => {
    console.error("review-response-error", diagnostic);
    throw new ReviewResponseError(`${label}を読み込めません。${reason}`, diagnostic);
  };
  try { response = await fetch(url, { cache: "no-store", signal }); }
  catch (error) {
    if (signal?.aborted) throw error;
    return fail({ url, stage: "fetch", status: null, contentType: null, bodyBytes: null, declaredContentLength: null }, "ローカル比較サーバーに接続できません。復旧後に再読み込みしてください。");
  }
  signal?.throwIfAborted();
  const base = { url, status: response.status, contentType: response.headers.get("content-type"), declaredContentLength: response.headers.get("content-length") };
  let text: string;
  try { text = await response.text(); }
  catch (error) {
    if (signal?.aborted) throw error;
    return fail({ ...base, stage: "read", bodyBytes: null }, "応答の受信が途中で切れました。再読み込みしてください。");
  }
  signal?.throwIfAborted();
  const bodyBytes = new TextEncoder().encode(text).byteLength;
  if (!response.ok) {
    return fail({ ...base, stage: "http", bodyBytes }, url.startsWith("/api/") && response.status >= 500
      ? `ローカル比較APIが利用できません（HTTP ${response.status}）。復旧後に再読み込みしてください。`
      : `比較情報の配信エラーです（HTTP ${response.status}）。`);
  }
  if (!text.trim()) return fail({ ...base, stage: "empty", bodyBytes }, "比較情報の応答が空です。再読み込みしてください。");
  const type = (base.contentType ?? "").split(";", 1)[0].trim().toLowerCase();
  if (type !== "application/json" && !type.endsWith("+json")) {
    return fail({ ...base, stage: "type", bodyBytes }, "比較情報の配信形式が正しくありません。再読み込みしてください。");
  }
  try { return JSON.parse(text); }
  catch { return fail({ ...base, stage: "parse", bodyBytes }, "比較情報が途中で切れているか、壊れています。再読み込みしてください。"); }
}
