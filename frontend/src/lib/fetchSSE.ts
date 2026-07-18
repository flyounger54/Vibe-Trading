import { authHeaders } from "@/lib/apiAuth";
import { ApiError, apiErrorFromResponse } from "@/lib/apiTransport";

type Listener = (event: MessageEvent<string>) => void;

interface StreamOptions {
  lastEventId?: string | null;
}

/**
 * EventSource-compatible facade backed by fetch so credentials stay in the
 * Authorization header and never appear in URLs, browser history, or logs.
 */
export class AuthenticatedEventStream {
  onopen: (() => void) | null = null;
  onerror: ((event?: unknown) => void) | null = null;

  private readonly controller = new AbortController();
  private readonly listeners = new Map<string, Set<Listener>>();
  private closed = false;

  constructor(readonly url: string, options: StreamOptions = {}) {
    void this.consume(options.lastEventId ?? null);
  }

  addEventListener(type: string, listener: EventListener): void {
    const listeners = this.listeners.get(type) ?? new Set<Listener>();
    listeners.add(listener as Listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener as Listener);
  }

  close(): void {
    this.closed = true;
    this.controller.abort();
    this.listeners.clear();
  }

  private dispatch(type: string, data: string, lastEventId: string): void {
    const event = new MessageEvent(type, { data, lastEventId });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  private async consume(lastEventId: string | null): Promise<void> {
    try {
      const headers: Record<string, string> = {
        Accept: "text/event-stream",
        "Cache-Control": "no-cache",
        ...authHeaders(),
      };
      if (lastEventId) headers["Last-Event-ID"] = lastEventId;
      const response = await fetch(this.url, {
        method: "GET",
        headers,
        signal: this.controller.signal,
        credentials: "same-origin",
      });
      if (!response.ok) throw await apiErrorFromResponse(response);
      if (!response.body) {
        throw new ApiError("The API server returned an empty event stream", response.status, { kind: "parse" });
      }
      const contentType = response.headers.get("Content-Type") ?? "";
      if (!contentType.includes("text/event-stream")) {
        throw new ApiError("The API server returned an invalid event stream", response.status, { kind: "parse" });
      }
      this.onopen?.();

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let eventType = "message";
      let eventId = "";
      let dataLines: string[] = [];

      const flush = () => {
        if (dataLines.length > 0) this.dispatch(eventType, dataLines.join("\n"), eventId);
        eventType = "message";
        eventId = "";
        dataLines = [];
      };

      while (!this.closed) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const lines = buffer.split(/\r?\n/);
        buffer = done ? "" : (lines.pop() ?? "");
        for (const line of lines) {
          if (line === "") {
            flush();
          } else if (!line.startsWith(":")) {
            const separator = line.indexOf(":");
            const field = separator < 0 ? line : line.slice(0, separator);
            const valuePart = separator < 0 ? "" : line.slice(separator + 1).replace(/^ /, "");
            if (field === "event") eventType = valuePart || "message";
            else if (field === "id") eventId = valuePart;
            else if (field === "data") dataLines.push(valuePart);
          }
        }
        if (done) {
          flush();
          break;
        }
      }
      if (!this.closed) this.onerror?.(new Error("SSE stream closed"));
    } catch (error) {
      if (!this.closed && !(error instanceof DOMException && error.name === "AbortError")) {
        this.onerror?.(error);
      }
    }
  }
}
