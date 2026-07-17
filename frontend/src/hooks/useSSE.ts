/**
 * SSE Hook — auto-reconnect + exponential backoff + LRU dedup + Last-Event-ID resume.
 */

import { useCallback, useRef } from "react";

type EventHandler = (data: Record<string, unknown>) => void;
type Handlers = Record<string, EventHandler>;

export type SSEStatus = "disconnected" | "connected" | "reconnecting";

interface SSEConfig {
  initialRetryMs?: number;
  maxRetryMs?: number;
  backoffFactor?: number;
  dedupeCapacity?: number;
}

const DEFAULTS: Required<SSEConfig> = {
  initialRetryMs: 1000,
  maxRetryMs: 30000,
  backoffFactor: 2,
  dedupeCapacity: 500,
};

export function useSSE(config?: SSEConfig) {
  const opts = { ...DEFAULTS, ...config };
  const sourceRef = useRef<EventSource | null>(null);
  const handlersRef = useRef<Handlers>({});
  const urlRef = useRef<string>("");
  const closedRef = useRef(true);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastEventIdRef = useRef<string | null>(null);
  const statusRef = useRef<SSEStatus>("disconnected");
  const onStatusChangeRef = useRef<((s: SSEStatus) => void) | null>(null);

  // LRU dedup set
  const seenIdsRef = useRef<Set<string>>(new Set());
  const seenOrderRef = useRef<string[]>([]);

  const trackEventId = useCallback((eventId: string): boolean => {
    if (!eventId) return false;
    const seen = seenIdsRef.current;
    const order = seenOrderRef.current;
    if (seen.has(eventId)) return true; // duplicate
    seen.add(eventId);
    order.push(eventId);
    if (order.length > opts.dedupeCapacity) {
      const oldest = order.shift()!;
      seen.delete(oldest);
    }
    return false;
  }, [opts.dedupeCapacity]);

  const setStatus = useCallback((s: SSEStatus) => {
    statusRef.current = s;
    onStatusChangeRef.current?.(s);
  }, []);

  const buildUrl = useCallback((baseUrl: string) => {
    const sep = baseUrl.includes("?") ? "&" : "?";
    if (lastEventIdRef.current) {
      return `${baseUrl}${sep}Last-Event-ID=${encodeURIComponent(lastEventIdRef.current)}`;
    }
    return baseUrl;
  }, []);

  const listenersRef = useRef<Array<[string, EventListener]>>([]);

  const cleanupSource = useCallback((source: EventSource) => {
    for (const [type, fn] of listenersRef.current) {
      source.removeEventListener(type, fn);
    }
    listenersRef.current = [];
    source.onopen = null;
    source.onerror = null;
    source.close();
  }, []);

  const doConnect = useCallback(() => {
    if (closedRef.current) return;

    if (sourceRef.current) {
      cleanupSource(sourceRef.current);
      sourceRef.current = null;
    }

    const url = buildUrl(urlRef.current);
    const source = new EventSource(url);
    sourceRef.current = source;

    source.onopen = () => {
      retryCountRef.current = 0;
      setStatus("connected");
    };

    const knownTypes = [
      "text_delta", "reasoning_delta", "stream_reset", "thinking_done", "tool_call", "tool_result", "compact",
      "tool_heartbeat", "tool_progress", "llm_usage",
      "swarm.started", "swarm.event",
      "attempt.created", "attempt.started", "attempt.completed", "attempt.failed",
      "message.received", "session.created",
      "goal.created", "goal.evidence", "goal.updated",
      "mandate.proposal", "mandate.committed", "live.halted", "live.resumed", "live.action",
      "heartbeat", "done",
    ];

    const handleRaw = (eventType: string, raw: MessageEvent) => {
      if (raw.lastEventId) {
        lastEventIdRef.current = raw.lastEventId;
      }
      if (raw.lastEventId && trackEventId(raw.lastEventId)) return;

      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(raw.data);
      } catch {
        parsed = { raw: raw.data };
      }

      const handler = handlersRef.current[eventType] ?? handlersRef.current["message"];
      handler?.(parsed);
    };

    const listeners: Array<[string, EventListener]> = [];
    for (const eventType of knownTypes) {
      const fn = (e: Event) => handleRaw(eventType, e as MessageEvent);
      source.addEventListener(eventType, fn);
      listeners.push([eventType, fn]);
    }
    listenersRef.current = listeners;

    source.onerror = () => {
      if (closedRef.current) return;
      cleanupSource(source);
      sourceRef.current = null;
      scheduleReconnect();
    };
  }, [buildUrl, cleanupSource, trackEventId, setStatus]);

  const scheduleReconnect = useCallback(() => {
    if (closedRef.current) return;
    retryCountRef.current += 1;
    const delay = Math.min(
      opts.initialRetryMs * Math.pow(opts.backoffFactor, retryCountRef.current - 1),
      opts.maxRetryMs,
    );
    setStatus("reconnecting");
    handlersRef.current["reconnect"]?.({ attempt: retryCountRef.current, delayMs: delay });

    retryTimerRef.current = setTimeout(() => {
      retryTimerRef.current = null;
      doConnect();
    }, delay);
  }, [opts.initialRetryMs, opts.backoffFactor, opts.maxRetryMs, setStatus, doConnect]);

  const connect = useCallback((url: string, handlers: Handlers) => {
    closedRef.current = true;
    if (sourceRef.current) {
      cleanupSource(sourceRef.current);
      sourceRef.current = null;
    }
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }

    urlRef.current = url;
    handlersRef.current = handlers;
    closedRef.current = false;
    retryCountRef.current = 0;
    lastEventIdRef.current = null;
    seenIdsRef.current.clear();
    seenOrderRef.current.length = 0;

    doConnect();
  }, [cleanupSource, doConnect]);

  const disconnect = useCallback(() => {
    closedRef.current = true;
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    if (sourceRef.current) {
      cleanupSource(sourceRef.current);
      sourceRef.current = null;
    }
    setStatus("disconnected");
  }, [cleanupSource, setStatus]);

  const getStatus = useCallback(() => statusRef.current, []);

  const onStatusChange = useCallback((cb: (s: SSEStatus) => void) => {
    onStatusChangeRef.current = cb;
  }, []);

  return { connect, disconnect, getStatus, onStatusChange };
}
