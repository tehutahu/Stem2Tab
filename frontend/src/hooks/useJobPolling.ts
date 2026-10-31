import { useEffect, useState } from "react";
import { API_BASE } from "../config";

export interface JobStatus {
  job_id: string;
  status: string;
  progress?: number;
  files?: string[];
  result?: unknown;
  error?: string | null;
}
export interface JobPollingState { data?: JobStatus; isLoading: boolean; error?: string }
const statuses = ["PENDING", "STARTED", "SUCCESS", "FAILURE", "RETRY", "REVOKED"];
const terminal = new Set(["SUCCESS", "FAILURE", "REVOKED"]);

export function useJobPolling(jobId?: string, intervalMs = 2000): JobPollingState {
  const [state, setState] = useState<JobPollingState & { id?: string }>({ isLoading: false });
  useEffect(() => {
    if (!jobId) return;
    let active = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const fetchStatus = async (): Promise<void> => {
      setState(previous => ({ ...(previous.id === jobId ? previous : {}), id: jobId, isLoading: true }));
      let done = false;
      try {
        const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}`, { signal: controller.signal });
        if (!response.ok) throw new Error(`Job status request failed (${response.status})`);
        const json = await response.json() as JobStatus;
        if (!active) return;
        if (json.job_id !== jobId || !statuses.includes(json.status) || (json.files !== undefined && (!Array.isArray(json.files) || !json.files.every(f => typeof f === "string")))) {
          throw new Error("Job status response does not match the selected job");
        }
        done = terminal.has(json.status);
        setState({ id: jobId, data: json, isLoading: false });
      } catch (cause) {
        if (active && !controller.signal.aborted) setState(previous => ({ ...previous, id: jobId, isLoading: false, error: String(cause) }));
      }
      if (active && !done) timer = setTimeout(() => { void fetchStatus(); }, intervalMs);
    };
    void fetchStatus();
    return () => { active = false; controller.abort(); if (timer !== undefined) clearTimeout(timer); };
  }, [jobId, intervalMs]);
  return jobId && state.id === jobId ? state : { isLoading: false };
}
