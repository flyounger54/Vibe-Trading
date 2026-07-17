import { useState, useEffect, useRef } from "react";

const DEBOUNCE_MS = 500;

export function useDraft(key: string): [string, (v: string) => void, () => void] {
  const [value, setValue] = useState(() => {
    try { return sessionStorage.getItem(key) ?? ""; } catch { return ""; }
  });
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    try {
      const stored = sessionStorage.getItem(key) ?? "";
      setValue(stored);
    } catch { /* ignore */ }
  }, [key]);

  useEffect(() => {
    return () => { clearTimeout(timerRef.current); };
  }, []);

  function setDraft(v: string) {
    setValue(v);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      try { sessionStorage.setItem(key, v); } catch { /* quota */ }
    }, DEBOUNCE_MS);
  }

  function clearDraft() {
    setValue("");
    clearTimeout(timerRef.current);
    try { sessionStorage.removeItem(key); } catch { /* ignore */ }
  }

  return [value, setDraft, clearDraft];
}
