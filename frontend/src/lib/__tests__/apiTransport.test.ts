import { ApiError, apiErrorFromResponse, requestJson } from "../apiTransport";
import { setApiAuthKey } from "../apiAuth";

describe("API transport", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("preserves the stable v1 error envelope and request metadata", async () => {
    const response = new Response(JSON.stringify({
      code: "unavailable",
      message: "Market data is warming up",
      request_id: "req-123",
      retryable: true,
      details: { provider: "demo" },
    }), {
      status: 503,
      headers: { "Content-Type": "application/json", "Retry-After": "2" },
    });

    const error = await apiErrorFromResponse(response);

    expect(error).toMatchObject({
      status: 503,
      kind: "http",
      code: "unavailable",
      message: "Market data is warming up",
      requestId: "req-123",
      retryable: true,
      details: { provider: "demo" },
      retryAfterMs: 2_000,
    });
  });

  it("never exposes an HTML fallback as a JSON syntax error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<!doctype html><title>Vite</title>", {
      status: 200,
      headers: { "Content-Type": "text/html" },
    })));

    await expect(requestJson("/runs", { retries: 0 })).rejects.toMatchObject({
      name: "ApiError",
      kind: "parse",
      message: "The API server returned an invalid data response",
    });
  });

  it("retries a safe GET when the server marks the failure retryable", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        code: "unavailable",
        message: "try again",
        request_id: "req-retry",
        retryable: true,
      }), {
        status: 503,
        headers: { "Content-Type": "application/json", "Retry-After": "0" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(requestJson<{ ok: boolean }>("/health", { retries: 1 })).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not retry an unsafe request without an idempotency key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: "unavailable",
      message: "write failed",
      request_id: "req-write",
      retryable: true,
    }), { status: 503, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(requestJson("/sessions", { method: "POST", body: "{}", retries: 3 })).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("sends authentication only in the header and classifies explicit cancellation", async () => {
    setApiAuthKey("transport-secret");
    const controller = new AbortController();
    controller.abort();
    const fetchMock = vi.fn((_url: string, init: RequestInit) => {
      expect(new Headers(init.headers).get("Authorization")).toBe("Bearer transport-secret");
      return Promise.reject(new DOMException("aborted", "AbortError"));
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(requestJson("/runs", { signal: controller.signal, retries: 0 })).rejects.toMatchObject({
      kind: "cancelled",
      retryable: false,
    });
  });
});
