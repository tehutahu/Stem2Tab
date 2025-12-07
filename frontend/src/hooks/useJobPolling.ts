import { useEffect, useState } from "react";

export interface JobStatus {
  job_id: string;
  status: string;
  result?: unknown;
  error?: string | null;
}

export interface JobPollingState {
  data?: JobStatus;
  isLoading: boolean;
  error?: string;
}

export function useJobPolling(jobId?: string, intervalMs = 2000): JobPollingState {
  const [data, setData] = useState<JobStatus | undefined>();
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | undefined>();

  useEffect(() => {
    if (!jobId) {
      setData(undefined);
      setError(undefined);
      return undefined;
    }

    let active = true;
    const controller = new AbortController();

    const fetchStatus = async (): Promise<void> => {
      setIsLoading(true);
      try {
        const response = await fetch(`/api/v1/jobs/${jobId}`, { signal: controller.signal });
        if (!response.ok) {
          throw new Error(`Job status request failed (${response.status})`);
        }
        const json = (await response.json()) as JobStatus;
        if (active) {
          setData(json);
          setError(undefined);
        }
      } catch (err) {
        if (!active) {
          return;
        }
        if (err instanceof Error && err.name === "AbortError") {
          return;
        }
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        if (active) {
          setIsLoading(false);
        }
      }
    };

    void fetchStatus();
    const intervalId = window.setInterval(fetchStatus, intervalMs);

    return () => {
      active = false;
      controller.abort();
      window.clearInterval(intervalId);
    };
  }, [jobId, intervalMs]);

  return { data, isLoading, error };
}

