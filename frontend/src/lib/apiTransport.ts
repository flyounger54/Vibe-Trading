import type { ErrorCode, ErrorEnvelope } from "@/generated/api-types";
import { authHeaders } from "@/lib/apiAuth";

const API_BASE = "/api/v1";
const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_RETRIES = 2;
const RETRY_BASE_MS = 500;

export type ApiErrorKind = "http" | "network" | "timeout" | "cancelled" | "parse";

interface ApiErrorOptions {
  kind?: ApiErrorKind;
  code?: ErrorCode;
  requestId?: string;
  retryable?: boolean;
  details?: unknown;
  retryAfterMs?: number;
  cause?: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly kind: ApiErrorKind;
  readonly code?: ErrorCode;
  readonly requestId?: string;
  readonly retryable: boolean;
  readonly details?: unknown;
  readonly retryAfterMs?: number;

  constructor(message: string, status: number, options: ApiErrorOptions = {}) {
    super(message);
    this.name = "ApiError";
    if (options.cause !== undefined) (this as Error & { cause?: unknown }).cause = options.cause;
    this.status = status;
    this.kind = options.kind ?? (status > 0 ? "http" : "network");
    this.code = options.code;
    this.requestId = options.requestId;
    this.retryable = options.retryable ?? false;
    this.details = options.details;
    this.retryAfterMs = options.retryAfterMs;
  }
}

export const AUTH_REQUIRED_MESSAGE =
  "API access requires a Bearer key, including on localhost. Add API_AUTH_KEY in Settings or use the key generated at ~/.vibe-trading/security/api.key.";

export function isAuthRequiredError(error: unknown): boolean {
  return error instanceof ApiError && (
    error.code === "unauthorized"
    || error.code === "forbidden"
    || error.status === 401
    || error.status === 403
  );
}

export interface RequestOptions extends RequestInit {
  timeout?: number;
  retries?: number;
}

function apiUrl(path: string): string {
  if (path.startsWith(`${API_BASE}/`) || path === API_BASE) return path;
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ErrorEnvelope>;
  return typeof candidate.code === "string"
    && typeof candidate.message === "string"
    && typeof candidate.request_id === "string";
}

function retryAfterMs(response: Response): number | undefined {
  const raw = response.headers.get("Retry-After");
  if (!raw) return undefined;
  const seconds = Number(raw);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1_000);
  const at = Date.parse(raw);
  return Number.isFinite(at) ? Math.max(0, at - Date.now()) : undefined;
}

function legacyDetail(body: unknown): { message?: string; details?: unknown } {
  if (!body || typeof body !== "object") return {};
  const value = body as { detail?: unknown; message?: unknown };
  if (typeof value.message === "string") return { message: value.message, details: value.detail };
  if (typeof value.detail === "string") return { message: value.detail };
  if (value.detail !== undefined) return { message: "Request validation failed", details: value.detail };
  return {};
}

async function responsePayload(response: Response): Promise<{ body?: unknown; text: string }> {
  const text = await response.text();
  if (!text) return { text };
  try {
    return { body: JSON.parse(text), text };
  } catch {
    return { text };
  }
}

export async function apiErrorFromResponse(response: Response): Promise<ApiError> {
  const { body, text } = await responsePayload(response);
  const requestId = response.headers.get("X-Request-ID") ?? undefined;
  const waitMs = retryAfterMs(response);

  let message = `Request failed (HTTP ${response.status})`;
  let code: ErrorCode | undefined;
  let details: unknown;
  let retryable = [429, 502, 503, 504].includes(response.status);

  if (isErrorEnvelope(body)) {
    message = body.message;
    code = body.code;
    details = body.details;
    retryable = body.retryable ?? retryable;
  } else {
    const legacy = legacyDetail(body);
    message = legacy.message ?? message;
    details = legacy.details;
    if (!body && text.trim().startsWith("<")) {
      message = "The API server returned a web page instead of data. Check that the API server is running and the Web UI proxy is configured.";
    }
  }

  if (response.status === 401 || response.status === 403) message = AUTH_REQUIRED_MESSAGE;

  return new ApiError(message, response.status, {
    kind: "http",
    code,
    requestId: requestId ?? (isErrorEnvelope(body) ? body.request_id : undefined),
    retryable,
    details,
    retryAfterMs: waitMs,
  });
}

function normalizeThrown(error: unknown, externalSignal: AbortSignal | null, timeoutSignal: AbortSignal): ApiError {
  if (error instanceof ApiError) return error;
  if (externalSignal?.aborted) {
    return new ApiError("Request cancelled", 0, { kind: "cancelled", cause: error });
  }
  if (timeoutSignal.aborted || (error instanceof DOMException && error.name === "TimeoutError")) {
    return new ApiError("Request timed out", 0, { kind: "timeout", retryable: true, cause: error });
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return new ApiError("Request cancelled", 0, { kind: "cancelled", cause: error });
  }
  return new ApiError("Unable to reach the API server", 0, {
    kind: "network",
    retryable: true,
    cause: error,
  });
}

function canRetry(method: string, headers: Headers): boolean {
  return method === "GET" || method === "HEAD" || headers.has("Idempotency-Key");
}

function abortableDelay(ms: number, signal?: AbortSignal | null): Promise<void> {
  if (signal?.aborted) return Promise.reject(new ApiError("Request cancelled", 0, { kind: "cancelled" }));
  return new Promise((resolve, reject) => {
    const cleanup = () => signal?.removeEventListener("abort", onAbort);
    const timer = window.setTimeout(() => {
      cleanup();
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(timer);
      cleanup();
      reject(new ApiError("Request cancelled", 0, { kind: "cancelled" }));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { headers: inputHeaders, timeout = DEFAULT_TIMEOUT_MS, retries = DEFAULT_RETRIES, ...init } = options;
  const headers = new Headers(inputHeaders);
  for (const [name, value] of Object.entries(authHeaders())) headers.set(name, value);
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const method = (init.method ?? "GET").toUpperCase();
  const retryAllowed = canRetry(method, headers);
  const maxRetries = retryAllowed ? Math.max(0, retries) : 0;
  let lastError: ApiError | undefined;

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    if (attempt > 0 && lastError) {
      const delay = lastError.retryAfterMs ?? RETRY_BASE_MS * 2 ** (attempt - 1);
      await abortableDelay(delay, init.signal);
    }

    const timeoutSignal = AbortSignal.timeout(timeout);
    const signal = init.signal ? AbortSignal.any([init.signal, timeoutSignal]) : timeoutSignal;
    try {
      const response = await fetch(apiUrl(path), {
        ...init,
        method,
        headers,
        signal,
        credentials: init.credentials ?? "same-origin",
      });
      if (!response.ok) throw await apiErrorFromResponse(response);
      if (response.status === 204) return {} as T;

      const { body, text } = await responsePayload(response);
      if (!text) return {} as T;
      if (body === undefined) {
        throw new ApiError("The API server returned an invalid data response", response.status, {
          kind: "parse",
          requestId: response.headers.get("X-Request-ID") ?? undefined,
        });
      }
      return body as T;
    } catch (error) {
      const normalized = normalizeThrown(error, init.signal ?? null, timeoutSignal);
      if (!retryAllowed || !normalized.retryable || attempt >= maxRetries) throw normalized;
      lastError = normalized;
    }
  }

  throw lastError ?? new ApiError("Request failed", 0, { kind: "network" });
}

export interface UploadResult {
  status: string;
  file_path: string;
  filename: string;
}

interface UploadOptions {
  signal?: AbortSignal;
  timeout?: number;
}

export async function uploadFile(
  file: File,
  onProgress?: (pct: number) => void,
  options: UploadOptions = {},
): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);

  if (!onProgress) {
    return requestJson<UploadResult>("/upload", {
      method: "POST",
      body: form,
      signal: options.signal,
      timeout: options.timeout,
      retries: 0,
    });
  }

  return new Promise<UploadResult>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      options.signal?.removeEventListener("abort", abort);
      fn();
    };
    const abort = () => xhr.abort();

    xhr.open("POST", `${API_BASE}/upload`);
    xhr.timeout = options.timeout ?? DEFAULT_TIMEOUT_MS;
    for (const [name, value] of Object.entries(authHeaders())) xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      const response = new Response(xhr.responseText, {
        status: xhr.status,
        headers: {
          "Content-Type": xhr.getResponseHeader("Content-Type") ?? "application/json",
          "X-Request-ID": xhr.getResponseHeader("X-Request-ID") ?? "",
          "Retry-After": xhr.getResponseHeader("Retry-After") ?? "",
        },
      });
      if (xhr.status < 200 || xhr.status >= 300) {
        void apiErrorFromResponse(response).then((error) => finish(() => reject(error)));
        return;
      }
      void responsePayload(response).then(({ body, text }) => {
        if (!text || body === undefined) {
          finish(() => reject(new ApiError("The API server returned an invalid upload response", xhr.status, { kind: "parse" })));
          return;
        }
        finish(() => resolve(body as UploadResult));
      });
    };
    xhr.onerror = () => finish(() => reject(new ApiError("Unable to reach the API server", 0, { kind: "network", retryable: true })));
    xhr.ontimeout = () => finish(() => reject(new ApiError("Upload timed out", 0, { kind: "timeout", retryable: true })));
    xhr.onabort = () => finish(() => reject(new ApiError("Upload cancelled", 0, { kind: "cancelled" })));
    options.signal?.addEventListener("abort", abort, { once: true });
    if (options.signal?.aborted) abort();
    else xhr.send(form);
  });
}
