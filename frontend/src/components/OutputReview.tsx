import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { API_BASE } from "../config";
import { workflowJson, WorkflowHttpError } from "../utils/workflowHttp";

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
export type OutputReviewInfo = { raw_note_count: number; zero_duration_count: number; positive_note_count: number; recorded_status: "needs_review" | "failed" };
export function parseOutputReview(value: unknown): OutputReviewInfo {
  if (!record(value) || value.kind !== "zero_duration" || value.notes_modified !== false ||
    (value.recorded_status !== "needs_review" && value.recorded_status !== "failed") ||
    typeof value.raw_note_count !== "number" || !Number.isInteger(value.raw_note_count) || value.raw_note_count < 1 || value.raw_note_count > 50000 ||
    typeof value.zero_duration_count !== "number" || !Number.isInteger(value.zero_duration_count) || value.zero_duration_count < 1 ||
    typeof value.positive_note_count !== "number" || !Number.isInteger(value.positive_note_count) || value.positive_note_count < 0 ||
    value.raw_note_count !== value.zero_duration_count + value.positive_note_count) throw new Error("保存出力の確認情報が不正です。");
  return { raw_note_count: value.raw_note_count, zero_duration_count: value.zero_duration_count,
    positive_note_count: value.positive_note_count, recorded_status: value.recorded_status };
}
type Preview = { kind: "idle" } | { kind: "rejected"; message: string } |
  { kind: "working" | "pending"; id: string } | { kind: "unknown" | "blocked"; id: string; message: string } |
  { kind: "completed"; id: string; trialId: string };

export default function OutputReview({ jobId, info }: { jobId: string; info: OutputReviewInfo }): JSX.Element {
  const [state, setState] = useState<Preview>({ kind: "idle" });
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const sending = useRef(false);
  const key = `stem2tab.output-review.${jobId}`;
  const root = `${API_BASE}/api/v1/transcriptions/${jobId}/review-previews`;
  const store = (id: string | null): void => { try { if (id) sessionStorage.setItem(key,id); else sessionStorage.removeItem(key); } catch { /* This page still retains the ID. */ } };
  const load = async (id: string, post: boolean): Promise<void> => {
    const current = ++generation.current;
    request.current?.abort(); const control = new AbortController(); request.current = control;
    sending.current = true; store(id); setState({ kind: "working", id });
    const data = new FormData(); data.append("request_id",id); data.append("policy","exclude_zero_duration");
    try {
      const value = await workflowJson(post ? root : `${root}/${id}`, { method: post ? "POST" : "GET", ...(post ? {body:data} : {}), signal:control.signal });
      if (control.signal.aborted || current !== generation.current) return;
      if (!record(value) || value.request_id !== id || value.source_job_id !== jobId || value.policy !== "exclude_zero_duration" ||
        value.raw_note_count !== info.raw_note_count || value.excluded_note_count !== info.zero_duration_count || value.retained_note_count !== info.positive_note_count)
        throw new Error("別の出力または候補IDの応答です。重複作成せず確認してください。");
      if (value.status === "completed" && typeof value.midi_trial_id === "string" && value.midi_trial_id === id && UUID.test(value.midi_trial_id))
        setState({kind:"completed",id,trialId:value.midi_trial_id});
      else if (value.status === "pending" && value.midi_trial_id === null) setState({kind:"pending",id});
      else if (value.status === "blocked" && value.midi_trial_id === null) {
        const error = value.error;
        const reason = record(error) && typeof error.message === "string" && error.message.trim()
          ? error.message : "候補の譜面化を停止しました。元の全出力と候補の記録は保持されています。";
        const code = record(error) && typeof error.code === "string" && /^[a-z0-9_]{1,64}$/.test(error.code) ? ` [${error.code}]` : "";
        setState({kind:"blocked",id,message:`候補の記録は受付済みです。${reason}${code} 同じ候補IDで確認できます。再推論は行いません。`});
      }
      else throw new Error("候補の状態を確認できません。");
    } catch (cause) {
      if (control.signal.aborted || current !== generation.current) return;
      if (cause instanceof WorkflowHttpError && (post ? cause.status >= 400 && cause.status < 500 && cause.status !== 409 : cause.status === 404)) {
        store(null); setState({kind:"rejected",message:`候補は受付されていません: ${cause.message}`});
      } else setState({kind:"unknown",id,message:`候補の作成結果は未確認です。同じ候補IDで確認してください: ${String(cause)}`});
    } finally { if (current === generation.current) sending.current = false; }
  };
  useEffect(() => {
    let prior: string | null = null; try { prior=sessionStorage.getItem(key); } catch { /* No persisted ID. */ }
    if (prior && UUID.test(prior)) void load(prior,false);
    return () => { generation.current++; request.current?.abort(); };
  }, [jobId]);
  const create = (): void => {
    if (sending.current || (state.kind !== "idle" && state.kind !== "rejected")) return;
    let id: string; try { id=crypto.randomUUID(); } catch { setState({kind:"rejected",message:"安全な候補IDを生成できません。"});return; }
    if (!UUID.test(id)) { setState({kind:"rejected",message:"安全な候補IDを生成できません。"});return; }
    void load(id,true);
  };
  return <section aria-label="保存された推論出力の確認">
    <h3>推論は完了しています。譜面化の前に確認してください</h3>
    <p>全{info.raw_note_count}音のうち、開始と終了が同時刻の音が{info.zero_duration_count}件あります。元の全音符・発音停止イベント・トークン・生MIDIは保持しています。自動で音を削除したり長さを補ったりしません。</p>
    {info.recorded_status === "failed" && <p>以前のfailed記録はそのまま残し、保存出力から確認しています。再推論はしていません。</p>}
    <p>明示して選ぶ場合だけ、ゼロ長{info.zero_duration_count}件を除いた{info.positive_note_count}音の別版をCPUで作れます。残す音の音高・開始・終了は保持します。この選択で音楽的な正解が確定するわけではありません。</p>
    <button disabled={state.kind !== "idle" && state.kind !== "rejected" || !info.positive_note_count || info.positive_note_count>2000} onClick={create}>ゼロ長{info.zero_duration_count}件を除いた別版を作成（原本保持）</button>
    {"id" in state && <p>候補ID: {state.id}</p>}
    {state.kind === "working" && <p role="status">保存済み出力から候補を確認中…</p>}
    {(state.kind === "unknown" || state.kind === "blocked" || state.kind === "rejected") && <p role="alert">{state.message}</p>}
    {state.kind === "pending" && <p role="status">候補を作成中です。再推論は行っていません。</p>}
    {(state.kind === "unknown" || state.kind === "pending" || state.kind === "blocked") && <button onClick={()=>{void load(state.id,false);}}>同じ候補IDを確認</button>}
    {state.kind === "completed" && <p><Link to={`/midi-trial/saved/${state.trialId}?view=staff&spacing=time&tempo_source_job=${jobId}`}>{info.positive_note_count}音の別版を試聴・譜面で確認</Link></p>}
  </section>;
}
