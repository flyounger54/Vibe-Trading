import { useCallback, useEffect, useRef, useState } from "react";

interface RetryableFetch<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  retry: () => void;
}

export function useRetryableFetch<T>(fetcher: (signal: AbortSignal) => Promise<T>, deps: unknown[] = []): RetryableFetch<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const ctrlRef = useRef<AbortController | null>(null);

  const run = useCallback(() => {
    ctrlRef.current?.abort();
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    setLoading(true);
    setError(null);

    fetcher(ctrl.signal)
      .then((result) => {
        if (!ctrl.signal.aborted) {
          setData(result);
          setError(null);
        }
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Unknown error");
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetcher, attempt, ...deps]);

  useEffect(() => {
    run();
    return () => { ctrlRef.current?.abort(); };
  }, [run]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);

  return { data, error, loading, retry };
}
