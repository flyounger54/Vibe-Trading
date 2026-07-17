import { useEffect, useRef } from "react";

export function useAbortController() {
  const ctrlRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      ctrlRef.current?.abort();
    };
  }, []);

  function getSignal(): AbortSignal {
    ctrlRef.current?.abort();
    ctrlRef.current = new AbortController();
    return ctrlRef.current.signal;
  }

  return { getSignal };
}
