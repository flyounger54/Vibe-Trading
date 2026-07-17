import { AuthenticatedEventStream } from "../fetchSSE";
import { setApiAuthKey } from "../apiAuth";

describe("AuthenticatedEventStream", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the long-term key only in Authorization and parses SSE", async () => {
    setApiAuthKey("stream-secret");
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('id: evt-1\nevent: progress\ndata: {"n":1}\n\n'));
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const received: MessageEvent[] = [];
    const stream = new AuthenticatedEventStream("/api/v1/jobs/abc/stream", { lastEventId: "evt-0" });
    stream.addEventListener("progress", ((event: MessageEvent) => received.push(event)) as EventListener);

    await vi.waitFor(() => expect(received).toHaveLength(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(url).toBe("/api/v1/jobs/abc/stream");
    expect(url).not.toContain("stream-secret");
    expect(headers.get("Authorization")).toBe("Bearer stream-secret");
    expect(headers.get("Last-Event-ID")).toBe("evt-0");
    expect(received[0].lastEventId).toBe("evt-1");
    expect(JSON.parse(received[0].data)).toEqual({ n: 1 });
    stream.close();
  });

  it("omits Authorization when no key is configured", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new ReadableStream({ start: (controller) => controller.close() }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const stream = new AuthenticatedEventStream("/events");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.has("Authorization")).toBe(false);
    stream.close();
  });

  it("surfaces non-success responses through onerror", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("denied", { status: 401 })));
    const errors: unknown[] = [];
    const stream = new AuthenticatedEventStream("/events");
    stream.onerror = (error) => errors.push(error);
    await vi.waitFor(() => expect(errors).toHaveLength(1));
    expect(String(errors[0])).toContain("401");
  });

  it("joins multiline SSE data without changing the URL", async () => {
    const payload = new TextEncoder().encode("event: note\ndata: first\ndata: second\n\n");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(new ReadableStream({
      start(controller) { controller.enqueue(payload); controller.close(); },
    }), { status: 200 })));
    const events: MessageEvent[] = [];
    const stream = new AuthenticatedEventStream("/events?replay=active");
    stream.addEventListener("note", ((event: MessageEvent) => events.push(event)) as EventListener);
    await vi.waitFor(() => expect(events).toHaveLength(1));
    expect(events[0].data).toBe("first\nsecond");
    expect(stream.url).toBe("/events?replay=active");
    stream.close();
  });

  it("aborts the fetch when closed", async () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn((_url, init: RequestInit) => {
      signal = init.signal as AbortSignal;
      return new Promise<Response>(() => {});
    }));
    const stream = new AuthenticatedEventStream("/events");
    await vi.waitFor(() => expect(signal).toBeDefined());
    stream.close();
    expect(signal?.aborted).toBe(true);
  });
});
