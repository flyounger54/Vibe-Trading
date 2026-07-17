import { authHeaders } from "@/lib/apiAuth";
import type {
  AddGoalEvidenceRequest as GeneratedAddGoalEvidenceRequest,
  AddGoalEvidenceResponse as GeneratedAddGoalEvidenceResponse,
  CreateGoalRequest as GeneratedCreateGoalRequest,
  GoalClaimResponse as GeneratedGoalClaim,
  GoalCriterionResponse as GeneratedGoalCriterion,
  GoalEvidenceResponse as GeneratedGoalEvidence,
  GoalRecordResponse as GeneratedGoalRecord,
  GoalSnapshotResponse as GeneratedGoalSnapshot,
  GoalStatus as GeneratedGoalStatus,
  MessageResponse as GeneratedMessageResponse,
  RiskTier as GeneratedGoalRiskTier,
  SessionResponse as GeneratedSessionResponse,
  UpdateGoalRequest as GeneratedUpdateGoalRequest,
  UpdateGoalResponse as GeneratedUpdateGoalResponse,
  UpdateGoalStatusRequest as GeneratedUpdateGoalStatusRequest,
  UpdateGoalStatusResponse as GeneratedUpdateGoalStatusResponse,
} from "@/generated/api-types";

const BASE = "/api/v1";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const AUTH_REQUIRED_MESSAGE =
  "API access requires a Bearer key, including on localhost. Add API_AUTH_KEY in Settings or use the key generated at ~/.vibe-trading/security/api.key.";

export function isAuthRequiredError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

async function errorFromResponse(res: Response): Promise<ApiError> {
  let detail = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    detail = body.detail || body.message || detail;
  } catch { /* ignore */ }
  if (res.status === 401 || res.status === 403) {
    detail = AUTH_REQUIRED_MESSAGE;
  }
  return new ApiError(detail, res.status);
}

const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_RETRIES = 3;
const RETRY_BASE_MS = 1_000;

function isRetryable(status: number): boolean {
  return status >= 500 && status <= 599;
}

interface RequestOptions extends RequestInit {
  timeout?: number;
  retries?: number;
}

async function request<T>(path: string, options?: RequestOptions): Promise<T> {
  const { headers, timeout = DEFAULT_TIMEOUT_MS, retries = MAX_RETRIES, ...rest } = options ?? {};
  const mergedHeaders: Record<string, string> = { "Content-Type": "application/json", ...authHeaders() };
  if (headers) {
    new Headers(headers).forEach((value, key) => {
      mergedHeaders[key] = value;
    });
  }

  let lastError: ApiError | undefined;
  for (let attempt = 0; attempt <= retries; attempt++) {
    if (attempt > 0) {
      await new Promise((r) => setTimeout(r, RETRY_BASE_MS * 2 ** (attempt - 1)));
    }

    const timeoutSignal = AbortSignal.timeout(timeout);
    const merged = rest.signal
      ? AbortSignal.any([rest.signal, timeoutSignal])
      : timeoutSignal;

    try {
      const res = await fetch(`${BASE}${path}`, {
        ...rest,
        signal: merged,
        headers: mergedHeaders,
      });
      if (!res.ok) {
        const err = await errorFromResponse(res);
        if (isRetryable(res.status) && attempt < retries) {
          lastError = err;
          continue;
        }
        throw err;
      }
      const text = await res.text();
      return text ? JSON.parse(text) : ({} as T);
    } catch (e) {
      if (e instanceof ApiError) throw e;
      if (e instanceof DOMException && e.name === "TimeoutError") {
        throw new ApiError("Request timed out", 0);
      }
      if (e instanceof DOMException && e.name === "AbortError") {
        throw new ApiError("Request cancelled", 0);
      }
      throw e;
    }
  }
  throw lastError ?? new ApiError("Request failed after retries", 0);
}

export interface UploadResult {
  status: string;
  file_path: string;
  filename: string;
}

async function uploadFile(file: File, onProgress?: (pct: number) => void): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);

  if (!onProgress) {
    const res = await fetch(`${BASE}/upload`, { method: "POST", headers: authHeaders(), body: form });
    if (!res.ok) throw await errorFromResponse(res);
    return res.json();
  }

  return new Promise<UploadResult>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/upload`);
    const hdrs = authHeaders();
    for (const [k, v] of Object.entries(hdrs)) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        let detail = `HTTP ${xhr.status}`;
        try { const b = JSON.parse(xhr.responseText); detail = b.detail || b.message || detail; } catch { /* ignore */ }
        reject(new ApiError(detail, xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError("Upload failed", 0));
    xhr.send(form);
  });
}

function appendQueryParam(url: string, key: string, value: string): string {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
}

export const api = {
  uploadFile,
  listRuns: (limit?: number) => request<RunListItem[]>(`/runs${limit ? `?limit=${encodeURIComponent(String(limit))}` : ""}`),
  getRun: (id: string, params: RunDetailParams = {}) => {
    const q = new URLSearchParams();
    if (params.chart_payload) q.set("chart_payload", params.chart_payload);
    if (params.chart_symbol) q.set("chart_symbol", params.chart_symbol);
    const qs = q.toString();
    return request<RunData>(`/runs/${id}${qs ? `?${qs}` : ""}`);
  },
  getRunCode: (id: string) => request<Record<string, string>>(`/runs/${id}/code`),
  getRunPine: (id: string) => request<PineScriptResult>(`/runs/${id}/pine`),
  listSessions: () => request<SessionItem[]>("/sessions"),
  createSession: (title?: string) => request<SessionItem>("/sessions", { method: "POST", body: JSON.stringify({ title: title || "" }) }),
  deleteSession: (sid: string) => request<{ status: string }>(`/sessions/${sid}`, { method: "DELETE" }),
  renameSession: (sid: string, title: string) => request<{ status: string }>(`/sessions/${sid}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  sendMessage: (sid: string, content: string) => request<{ message_id: string; attempt_id: string }>(`/sessions/${sid}/messages`, { method: "POST", body: JSON.stringify({ content }) }),
  cancelSession: (sid: string) => request<{ status: string }>(`/sessions/${sid}/cancel`, { method: "POST" }),
  getSessionMessages: (sid: string) => request<MessageItem[]>(`/sessions/${sid}/messages`),
  createGoal: (sid: string, body: CreateGoalRequest) =>
    request<GoalSnapshot>(`/sessions/${sid}/goal`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getGoal: (sid: string) => request<GoalSnapshot>(`/sessions/${sid}/goal`),
  updateGoal: (sid: string, body: UpdateGoalRequest) =>
    request<UpdateGoalResponse>(`/sessions/${sid}/goal`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  addGoalEvidence: (sid: string, body: AddGoalEvidenceRequest) =>
    request<AddGoalEvidenceResponse>(`/sessions/${sid}/goal/evidence`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateGoalStatus: (sid: string, body: UpdateGoalStatusRequest) =>
    request<UpdateGoalStatusResponse>(`/sessions/${sid}/goal/status`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  sseUrl: (sid: string, options?: { replay?: "active" }) => {
    let url = `${BASE}/sessions/${sid}/events`;
    if (options?.replay) url = appendQueryParam(url, "replay", options.replay);
    return url;
  },

  // Swarm API
  listSwarmPresets: () => request<SwarmPreset[]>("/swarm/presets"),
  createSwarmRun: (preset_name: string, user_vars: Record<string, string>) =>
    request<{ id: string; status: string }>("/swarm/runs", {
      method: "POST",
      body: JSON.stringify({ preset_name, user_vars }),
    }),
  listSwarmRuns: () => request<SwarmRunSummary[]>("/swarm/runs"),
  getSwarmRun: (id: string) => request<Record<string, unknown>>(`/swarm/runs/${id}`),
  swarmSseUrl: (id: string) => `${BASE}/swarm/runs/${id}/events`,
  cancelSwarmRun: (id: string) =>
    request<{ status: string }>(`/swarm/runs/${id}/cancel`, { method: "POST" }),
  retrySwarmRun: (id: string) =>
    request<{ id: string; status: string; preset_name: string }>(`/swarm/runs/${id}/retry`, { method: "POST" }),
  getLLMSettings: () => request<LLMSettings>("/settings/llm"),
  updateLLMSettings: (settings: UpdateLLMSettingsRequest) =>
    request<LLMSettings>("/settings/llm", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  getDataSourceSettings: () => request<DataSourceSettings>("/settings/data-sources"),
  updateDataSourceSettings: (settings: UpdateDataSourceSettingsRequest) =>
    request<DataSourceSettings>("/settings/data-sources", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),

  // Alpha Zoo API
  listAlphas: (params: AlphaListParams = {}) => {
    const q = new URLSearchParams();
    if (params.zoo) q.set("zoo", params.zoo);
    if (params.theme) q.set("theme", params.theme);
    if (params.universe) q.set("universe", params.universe);
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<AlphaListResponse>(`/alpha/list${qs ? `?${qs}` : ""}`);
  },
  getAlpha: (alphaId: string) =>
    request<AlphaDetailResponse>(`/alpha/${encodeURIComponent(alphaId)}`),
  createAlphaBench: (body: AlphaBenchRequest) =>
    request<{ status: string; job_id: string }>("/alpha/bench", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  alphaBenchStreamUrl: (jobId: string) =>
    `${BASE}/alpha/bench/${encodeURIComponent(jobId)}/stream`,

  // Strategy Zoo API
  listStrategies: (params: StrategyListParams = {}) => {
    const q = new URLSearchParams();
    if (params.category) q.set("category", params.category);
    if (params.universe) q.set("universe", params.universe);
    if (params.risk) q.set("risk", params.risk);
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<StrategyListResponse>(`/strategy/list${qs ? `?${qs}` : ""}`);
  },
  getStrategy: (strategyId: string) =>
    request<StrategyDetailResponse>(`/strategy/${encodeURIComponent(strategyId)}`),
  createAlphaCompare: (body: AlphaCompareRequest) =>
    request<{ status: string; job_id: string }>("/alpha/compare", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  alphaCompareStreamUrl: (jobId: string) =>
    `${BASE}/alpha/compare/${encodeURIComponent(jobId)}/stream`,

  // Connector runtime channel — privileged surface actions (NOT agent tools).
  // commit is the ONLY action that writes a mandate; halt trips the kill switch.
  commitMandate: (body: CommitMandateRequest) =>
    request<CommitMandateResponse>("/mandate/commit", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  haltLive: (session_id?: string, broker?: string, reason?: string) =>
    request<HaltLiveResponse>("/live/halt", {
      method: "POST",
      body: JSON.stringify({ session_id, broker, reason }),
    }),
  // Read the persistent runtime status across all authorized brokers (SPEC §7.5).
  // Polled by the RunnerStatus panel; a plain authenticated GET, never a chat message.
  getLiveStatus: () => request<LiveStatus>("/live/status"),
  authorizeLive: (broker: string) =>
    request<LiveAuthorizeResponse>("/live/authorize", {
      method: "POST",
      body: JSON.stringify({ broker }),
    }),
  // Start/stop the persistent runner (SPEC §7.5). Privileged surface actions, not agent tools.
  startLiveRunner: (broker: string) =>
    request<LiveRunnerResponse>("/live/runner/start", {
      method: "POST",
      body: JSON.stringify({ broker }),
    }),
  stopLiveRunner: (broker: string) =>
    request<LiveRunnerResponse>("/live/runner/stop", {
      method: "POST",
      body: JSON.stringify({ broker }),
    }),

  // ML Training API
  listMLModels: (sortBy = "created_at") =>
    request<MLModelsResponse>(`/ml/models?sort_by=${sortBy}`),
  getMLModel: (modelId: string) =>
    request<MLModelDetailResponse>(`/ml/models/${encodeURIComponent(modelId)}`),
  deleteMLModel: (modelId: string) =>
    request<{ status: string; deleted: string }>(`/ml/models/${encodeURIComponent(modelId)}`, { method: "DELETE" }),
  startMLTrain: (body: MLTrainRequest) =>
    request<{ status: string; job_id: string }>("/ml/train", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  mlTrainStreamUrl: (jobId: string) =>
    `${BASE}/ml/train/${encodeURIComponent(jobId)}/stream`,
  listFeatureProfiles: () =>
    request<MLProfilesResponse>("/ml/profiles"),
  createFeatureProfile: (body: MLSelectFeaturesRequest) =>
    request<MLProfileCreateResponse>("/ml/profiles", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  compareMLModels: (modelIds: string[]) =>
    request<MLCompareResponse>("/ml/compare", {
      method: "POST",
      body: JSON.stringify({ model_ids: modelIds }),
    }),
  createEnsemble: (body: MLEnsembleRequest) =>
    request<{ status: string; ensemble_id: string }>("/ml/ensemble", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  checkModelHealth: (body: MLHealthRequest) =>
    request<MLHealthResponse>("/ml/health", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listExperiments: () =>
    request<MLExperimentsResponse>("/ml/experiments"),

  // --- Industry Chain dashboard ---
  listChainTemplates: () =>
    request<{ templates: ChainTemplate[] }>("/industry-chain/templates"),
  listChains: () =>
    request<{ chains: ChainSummary[] }>("/industry-chain/list"),
  getChain: (id: string) =>
    request<Chain>(`/industry-chain/${encodeURIComponent(id)}`),
  createChain: (body: CreateChainRequest) =>
    request<{ status: string; chain_id: string; chain: Chain }>("/industry-chain", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateChain: (id: string, body: UpdateChainRequest) =>
    request<{ status: string; chain: Chain }>(`/industry-chain/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteChain: (id: string) =>
    request<{ status: string }>(`/industry-chain/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  analyzeChain: (id: string, market?: string) =>
    request<{ status: string; chain_id: string; run_id: string }>(
      `/industry-chain/${encodeURIComponent(id)}/analyze`,
      { method: "POST", body: JSON.stringify({ market: market ?? null }) },
    ),
  getChainStatus: (id: string) =>
    request<ChainStatus>(`/industry-chain/${encodeURIComponent(id)}/status`),
  getChainHistory: (id: string) =>
    request<{ chain_id: string; snapshots: ChainSnapshot[] }>(
      `/industry-chain/${encodeURIComponent(id)}/history`,
    ),
  getChainSwarmDetail: (id: string) =>
    request<ChainSwarmDetail>(`/industry-chain/${encodeURIComponent(id)}/swarm-detail`),
  setChainSchedule: (id: string, schedule: string) =>
    request<{ status: string; chain_id: string; refresh_schedule: string }>(
      `/industry-chain/${encodeURIComponent(id)}/schedule`,
      { method: "PUT", body: JSON.stringify({ schedule }) },
    ),
  compareChains: (ids: string) =>
    request<ChainCompareResult>(`/industry-chain/compare?ids=${encodeURIComponent(ids)}`),
  listChainHypotheses: (id: string) =>
    request<{ chain_id: string; hypotheses: ChainHypothesis[] }>(
      `/industry-chain/${encodeURIComponent(id)}/hypotheses`,
    ),
  createChainHypothesis: (id: string, body: CreateHypothesisRequest) =>
    request<{ status: string; hypothesis: ChainHypothesis }>(
      `/industry-chain/${encodeURIComponent(id)}/hypotheses`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  exportChain: (id: string) =>
    request<{ chain_id: string; filename: string; markdown: string }>(
      `/industry-chain/${encodeURIComponent(id)}/export`,
    ),
};

// --- Swarm types ---

export interface SwarmPreset {
  name: string;
  title: string;
  description: string;
  agent_count: number;
  variables: { name: string; description: string; required: boolean }[];
}

export interface SwarmRunSummary {
  id: string;
  preset_name: string;
  status: string;
  created_at: string;
  task_count: number;
  completed_count: number;
}

export interface LLMProviderOption {
  name: string;
  label: string;
  api_key_env?: string | null;
  base_url_env: string;
  default_model: string;
  default_base_url: string;
  api_key_required: boolean;
  auth_type?: string;
  login_command?: string | null;
}

export interface LLMSettings {
  provider: string;
  model_name: string;
  base_url: string;
  api_key_env?: string | null;
  api_key_configured: boolean;
  api_key_hint?: string | null;
  api_key_required: boolean;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
  reasoning_effort: string;
  sse_timeout_seconds: number;
  env_path: string;
  providers: LLMProviderOption[];
}

export interface UpdateLLMSettingsRequest {
  provider: string;
  model_name: string;
  base_url: string;
  api_key?: string;
  clear_api_key?: boolean;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
  reasoning_effort?: string;
}

export interface DataSourceSettings {
  tushare_token_configured: boolean;
  tushare_token_hint?: string | null;
  baostock_supported: boolean;
  baostock_installed: boolean;
  baostock_message: string;
  env_path: string;
}

export interface UpdateDataSourceSettingsRequest {
  tushare_token?: string;
  clear_tushare_token?: boolean;
}

// --- Types matching backend API contracts ---

export interface RunListItem {
  run_id: string;
  status: string;
  created_at: string;
  prompt?: string;
  total_return?: number;
  sharpe?: number;
  codes?: string[];
  start_date?: string;
  end_date?: string;
}

export interface RunDetailParams {
  chart_payload?: "summary";
  chart_symbol?: string;
}

export interface PriceBar {
  time: string;
  timestamp?: string;
  code?: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TradeMarker {
  time: string;
  timestamp?: string;
  code?: string;
  side: "BUY" | "SELL";
  price: number;
  qty?: number;
  reason?: string;
  text?: string;
  stop_loss?: number;
  take_profit?: number;
}

export interface EquityPoint {
  time: string;
  equity: string | number;
  drawdown: string | number;
}

export interface ValidationData {
  monte_carlo?: {
    actual_sharpe: number;
    actual_max_dd: number;
    p_value_sharpe: number;
    p_value_max_dd: number;
    simulated_sharpe_mean: number;
    simulated_sharpe_std: number;
    simulated_sharpe_p5: number;
    simulated_sharpe_p95: number;
    n_simulations: number;
    n_trades: number;
    error?: string;
  };
  bootstrap?: {
    observed_sharpe: number;
    ci_lower: number;
    ci_upper: number;
    median_sharpe: number;
    prob_positive: number;
    confidence: number;
    n_bootstrap: number;
    error?: string;
  };
  walk_forward?: {
    n_windows: number;
    windows: Array<{
      window: number;
      start: string;
      end: string;
      return: number;
      sharpe: number;
      max_dd: number;
      trades: number;
      win_rate: number;
    }>;
    profitable_windows: number;
    consistency_rate: number;
    return_mean: number;
    return_std: number;
    sharpe_mean: number;
    sharpe_std: number;
    error?: string;
  };
}

export interface RunData {
  status: string;
  run_id: string;
  prompt?: string;
  elapsed_seconds?: number;
  run_directory?: string;
  run_stage?: string;
  run_context?: Record<string, unknown>;

  metrics?: BacktestMetrics;
  artifacts?: ArtifactInfo[];
  run_card?: RunCard;
  validation?: ValidationData;

  chart_symbols?: string[];
  price_series?: Record<string, PriceBar[]>;
  indicator_series?: Record<string, Record<string, IndicatorPoint[]>>;
  trade_markers?: TradeMarker[];
  equity_curve?: EquityPoint[];
  trade_log?: Array<Record<string, string>>;
  run_logs?: Array<{ source?: string; line_number?: number; message?: string }>;
}

export interface RunCard {
  schema_version?: string;
  generated_at?: string;
  run_dir?: string;
  backtest?: Record<string, unknown>;
  reproducibility?: Record<string, unknown>;
  data_sources?: string[];
  metrics?: Record<string, unknown>;
  validation?: unknown;
  warnings?: string[];
  artifacts?: RunCardArtifact[];
  [key: string]: unknown;
}

export interface RunCardArtifact {
  path: string;
  size_bytes: number;
  sha256: string;
}

export interface BacktestMetrics {
  final_value: number;
  total_return: number;
  annual_return: number;
  max_drawdown: number;
  sharpe: number;
  win_rate: number;
  trade_count: number;
  [key: string]: number;
}


export interface IndicatorPoint {
  time: string;
  value: number;
}

export interface ArtifactInfo {
  name: string;
  path: string;
  type: string;
  size: number;
  exists: boolean;
}

export interface PineScriptResult {
  exists: boolean;
  content: string | null;
}

export type SessionItem = GeneratedSessionResponse;

// --- Goal types ---

export type GoalStatus = GeneratedGoalStatus;
export type GoalRiskTier = Exclude<GeneratedGoalRiskTier, "live_trading_or_execution">;
export type GoalRecord = GeneratedGoalRecord;
export type GoalClaim = GeneratedGoalClaim;
export type GoalCriterion = GeneratedGoalCriterion;
export type GoalEvidence = GeneratedGoalEvidence;
export type GoalSnapshot = GeneratedGoalSnapshot;
export type CreateGoalRequest = GeneratedCreateGoalRequest;
export type AddGoalEvidenceRequest = GeneratedAddGoalEvidenceRequest;
export type UpdateGoalRequest = GeneratedUpdateGoalRequest;
export type UpdateGoalResponse = GeneratedUpdateGoalResponse;
export type AddGoalEvidenceResponse = GeneratedAddGoalEvidenceResponse;
export type UpdateGoalStatusRequest = GeneratedUpdateGoalStatusRequest;
export type UpdateGoalStatusResponse = GeneratedUpdateGoalStatusResponse;

// --- Alpha Zoo types ---

export interface AlphaListParams {
  zoo?: string;
  theme?: string;
  universe?: string;
  limit?: number;
}

export interface AlphaSummary {
  id: string;
  zoo: string;
  theme: string[];
  universe: string[];
  nickname?: string;
  decay_horizon?: number | null;
  min_warmup_bars?: number | null;
  requires_sector?: boolean;
}

export interface AlphaListResponse {
  status: string;
  alphas: AlphaSummary[];
  total: number;
  returned: number;
  truncated: boolean;
}

export interface AlphaDetail {
  id: string;
  zoo: string;
  module_path?: string;
  meta: Record<string, unknown>;
}

export interface AlphaDetailResponse {
  status: string;
  alpha: AlphaDetail;
  source_code: string;
}

export interface AlphaBenchRequest {
  zoo: string;
  universe: string;
  period: string;
  top?: number;
}

export interface AlphaBenchTopRow {
  id: string;
  ic_mean: number;
  ir: number;
  theme: string[];
  formula_latex: string;
  category: "alive" | "reversed" | "dead";
}

export interface AlphaBenchResult {
  alive: number;
  reversed: number;
  dead: number;
  skipped?: number;
  top5_by_ir: AlphaBenchTopRow[];
  dead_examples: AlphaBenchTopRow[];
  by_theme: Record<string, { alive: number; reversed: number; dead: number }>;
}

export interface AlphaCompareRequest {
  alpha_ids: string[];
  universe: string;
  period: string;
  /** One of: ir | ic_mean | ic_positive_ratio | ic_count (default ir). */
  sort?: string;
}

export interface AlphaCompareRow {
  rank: number;
  id: string;
  zoo: string;
  ic_mean: number;
  ic_std: number;
  ir: number;
  ic_positive_ratio: number;
  ic_count: number;
  /** `delta_<sort>_vs_best` — gap to the top-ranked alpha on the active metric. */
  [deltaKey: string]: number | string;
}

export interface AlphaCompareSkip {
  id: string;
  reason: string;
}

export interface AlphaCompareResult {
  universe: string;
  period: string;
  sort: string;
  n_compared: number;
  n_skipped: number;
  winner: string;
  ranking: AlphaCompareRow[];
  skipped: AlphaCompareSkip[];
}

// --- Strategy Zoo types ---

export interface StrategyListParams {
  category?: string;
  universe?: string;
  risk?: string;
  limit?: number;
}

export interface StrategySummary {
  id: string;
  category: string;
  nickname: string;
  description: string;
  universe: string[];
  frequency: string[];
  risk_profile: string;
  min_bars: number;
  reference: string;
  default_params: Record<string, unknown>;
  columns_required: string[];
  factors_used: string[];
}

export interface StrategyListResponse {
  strategies: StrategySummary[];
  total: number;
  health: { loaded: number; failed: number };
}

export interface StrategyDetail {
  id: string;
  category: string;
  module_path: string;
  meta: Record<string, unknown>;
}

export interface StrategyDetailResponse {
  strategy: StrategyDetail;
  source_code: string;
}

// --- Connector runtime channel types ---

/** One mandate profile inside a `mandate.proposal` event (SPEC Consent §1). */
export interface MandateProfile {
  ordinal: number;
  label: string;
  /** Concrete ticker list, or a structural universe descriptor (e.g. "tech_sector"). */
  universe: string[] | string;
  max_order_usd: number;
  daily_trade_cap: number;
  /** "none" for cash-only, otherwise a leverage descriptor/multiple. */
  leverage: string | number;
  instruments: string[];
  notes?: string;
}

/** Account block of a `mandate.proposal` event. */
export interface MandateProposalAccount {
  broker: string;
  type: string;
  funded_by: string;
}

/** Payload of the `mandate.proposal` SSE event (SPEC Consent §1). */
export interface MandateProposal {
  type?: string;
  proposal_id: string;
  session_id?: string;
  intent_normalized?: string;
  account?: MandateProposalAccount;
  ceilings_ref?: string;
  profiles: MandateProfile[];
  funding_note?: string;
  halt_note?: string;
  /** Present only when this proposal was triggered by a mandate breach (SPEC Consent §3). */
  reauth_for?: { breach_id?: string } | null;
}

/** Payload of the `mandate.committed` SSE event (SPEC Consent §1 COMMIT). */
export interface MandateCommitted {
  proposal_id?: string;
  mandate_id?: string;
  consent_record_id?: string;
  selected_ordinal?: number;
  broker?: string;
  /** Resolved limits, surfaced for the compact active-mandate badge. */
  max_order_usd?: number;
  daily_trade_cap?: number;
  expires_at?: string;
}

/** Payload of the `live.halted` SSE event (SPEC Consent §4). */
export interface LiveHalted {
  broker?: string | null;
  tripped_at?: string;
  by?: string;
  reason?: string;
}

/** Payload of the `live.action` SSE event (SPEC Consent §5 audit notify). */
export interface LiveAction {
  audit_id?: string;
  ts?: string;
  kind: string;
  intent_normalized?: string;
  outcome?: string;
  broker?: string;
  remote_tool?: string;
  error?: string | null;
}

export interface CommitMandateRequest {
  broker: string;
  proposal_id: string;
  selected_ordinal: number;
  /** Present only on the adjust path (SPEC Consent §3); null otherwise. */
  adjustments?: Record<string, unknown> | null;
  /** Explicit affirmative consent; the surface sets it on the user's click. */
  consent_ack: boolean;
  session_id?: string;
  account_ref?: string;
  lifetime_days?: number;
}

export interface CommitMandateResponse {
  mandate_id: string;
  consent_record_id: string;
  selected_ordinal?: number;
  broker?: string;
  max_order_usd?: number;
  daily_trade_cap?: number;
  expires_at?: string;
}

export interface HaltLiveResponse {
  halted: boolean;
  broker?: string | null;
  reason: string;
  sentinel: string;
}

export interface LiveAuthorizeRequest {
  broker: string;
}

export interface LiveAuthorizeResponse {
  broker: string;
  connector_profile: string;
  oauth_token_present: boolean;
  instruction: string;
  note?: string;
}

/** Mandate limits surfaced inside a `GET /live/status` broker entry (SPEC §7.5). */
export interface LiveMandateLimits {
  max_order_notional_usd?: number;
  max_total_exposure_usd?: number;
  max_leverage?: number;
  max_trades_per_day?: number;
  allowed_instruments?: string[];
  account_funding_usd?: number;
  [key: string]: unknown;
}

/** Active mandate block of a `GET /live/status` broker entry. */
export interface LiveMandateStatus {
  broker?: string;
  mandate_id?: string;
  account_ref?: string;
  created_at?: string;
  limits?: LiveMandateLimits;
  /** ISO timestamp the mandate auto-expires (SPEC §7.5 #7 proactive expiry). */
  expires_at?: string;
  expires_in_seconds?: number | null;
  expired?: boolean;
}

/** Runner liveness block of a `GET /live/status` broker entry (SPEC §7.5 #3). */
export interface LiveRunnerLiveness {
  broker?: string;
  alive: boolean;
  /** Unix epoch seconds of the last heartbeat tick; null if the runner never started. */
  last_tick?: number | string | null;
  last_tick_age_seconds?: number | null;
}

export interface LiveBrokerAuthStatus {
  broker: string;
  oauth_token_present: boolean;
  is_live_broker: boolean;
}

/** One broker entry in the `GET /live/status` response. */
export interface LiveBrokerStatus {
  auth: LiveBrokerAuthStatus;
  mandate?: LiveMandateStatus | null;
  runner: LiveRunnerLiveness;
  halted: boolean;
}

/** Response of `GET /live/status` (SPEC §7.5 runner status panel + C2). */
export interface LiveStatus {
  brokers: LiveBrokerStatus[];
  global_halted: boolean;
}

/** Response of `POST /live/runner/start|stop`. */
export interface LiveRunnerResponse {
  broker: string;
  started?: boolean;
  already_running?: boolean;
  stopped?: boolean;
  was_running?: boolean;
}

export type MessageItem = GeneratedMessageResponse;

// ---------------------------------------------------------------------------
// ML Training types
// ---------------------------------------------------------------------------

export interface MLModelSummary {
  model_id: string;
  model_type: string;
  universe: string;
  period: string;
  label_horizon: number;
  label_type: string;
  benchmark: string | null;
  n_features: number;
  n_train_samples: number;
  cv_ic_mean: number | null;
  cv_auc_mean: number | null;
  overfit_warning: boolean;
  created_at: string;
}

export interface MLModelsResponse {
  status: string;
  models: MLModelSummary[];
  total: number;
}

export interface MLModelDetailResponse {
  status: string;
  metadata: Record<string, unknown>;
  train_log: Record<string, unknown>[];
}

export interface MLTrainRequest {
  universe: string;
  period: string;
  zoo?: string;
  feature_profile_id?: string;
  model_type?: string;
  model_params?: Record<string, unknown>;
  label_horizon?: number;
  label_type?: string;
  benchmark?: string;
  cost_bps?: number;
  n_splits?: number;
  model_id?: string;
}

export interface MLTrainProgress {
  stage: string;
  message?: string;
  model_type?: string;
  universe?: string;
  model_id?: string;
}

export interface MLTrainResult {
  model_id: string;
  model_path: string;
  n_features: number;
  n_train_samples: number;
  cv_summary: Record<string, number>;
  overfit_warning: boolean;
  top_features: Record<string, number>;
  wall_seconds: number;
}

export interface MLSelectFeaturesRequest {
  universe: string;
  period: string;
  zoo?: string;
  methods?: string[];
  profile_id?: string;
}

export interface MLProfileSummary {
  profile_id: string;
  zoo: string;
  universe: string;
  n_selected: number;
  methods: string[];
  created_at: string;
}

export interface MLProfilesResponse {
  status: string;
  profiles: MLProfileSummary[];
  total: number;
}

export interface MLProfileCreateResponse {
  status: string;
  profile_id: string;
  n_selected: number;
  factors: string[];
  methods: string[];
}

export interface MLCompareRow {
  model_id: string;
  model_type: string;
  horizon: number;
  label_type: string;
  benchmark: string;
  n_features: number;
  test_ic_mean: number | null;
  test_auc_mean: number | null;
  overfit_warning: boolean;
  [key: string]: unknown;
}

export interface MLCompareResponse {
  status: string;
  comparison: MLCompareRow[];
}

export interface MLEnsembleRequest {
  model_ids: string[];
  method?: string;
  weights?: number[];
  ensemble_id?: string;
}

export interface MLHealthRequest {
  model_id?: string;
  recent_period: string;
  base_id?: string;
}

export interface MLHealthResponse {
  status: string;
  model_id?: string;
  train_ic?: number;
  recent_ic?: number;
  drift_score?: number;
  retrain_recommended?: boolean;
  reports?: Array<{
    model_id: string;
    drift_score: number;
    retrain_recommended: boolean;
  }>;
  // version drift fields
  base_id?: string;
  ic_trend?: number[];
  sustained_decline?: boolean;
  sudden_drop?: boolean;
  recommendation?: string;
}

export interface MLExperimentsResponse {
  status: string;
  experiments: Array<{
    name: string;
    universe: string;
    model_type: string;
    horizon: number | string;
    label_type: string;
    has_schedule: boolean;
  }>;
}

// --- Industry Chain dashboard types ---

export interface ChainTemplate {
  key: string;
  name: string;
  name_en: string;
  description: string;
  segment_count: number;
}

export interface ChainTicker {
  code: string;
  name: string;
  market: string;
  score: number | null;
  tier: string;
  classification: string;
  confidence: string;
  key_products: string;
  red_team_note: string;
}

export interface ChainSegment {
  segment_id: string;
  name: string;
  name_en: string;
  order: number;
  positioning: string;
  value_weight: string;
  localization_rate: string;
  international_competition: string;
  domestic_competition: string;
  barrier_type: string;
  barrier_description: string;
  chokepoint_score: Record<string, number>;
  chokepoint_total: number | null;
  status: string;
  tickers: ChainTicker[];
}

export interface ChainOverview {
  structure_summary: string;
  lifecycle_stage: string;
  prosperity_score: number | null;
  sector_score: number | null;
  core_targets: ChainTicker[];
}

export interface Chain {
  chain_id: string;
  name: string;
  name_en: string;
  description: string;
  market: string;
  status: string;
  template_key: string;
  swarm_run_id: string;
  refresh_schedule: string;
  created_at: string;
  updated_at: string;
  overview: ChainOverview;
  segments: ChainSegment[];
}

export interface ChainSummary {
  chain_id: string;
  name: string;
  name_en: string;
  description: string;
  market: string;
  status: string;
  template_key: string;
  segment_count: number;
  lifecycle_stage: string;
  prosperity_score: number | null;
  refresh_schedule: string;
  updated_at: string;
}

export interface ChainStatus {
  chain_id: string;
  status: string;
  run_id: string;
  run_status?: string;
  ingested?: boolean;
  task_count?: number;
  completed_count?: number;
}

export interface ChainSnapshot {
  recorded_at: string;
  lifecycle_stage: string;
  prosperity_score: number | null;
  sector_score: number | null;
  segment_scores: Record<string, number>;
}

export interface CreateChainRequest {
  template_key?: string;
  name?: string;
  segment_names?: string[];
  description?: string;
  market?: string;
}

export interface UpdateChainRequest {
  name?: string;
  description?: string;
  market?: string;
  segments?: ChainSegment[];
}

export interface SwarmTaskSummary {
  task_id: string;
  agent_id: string;
  agent_role: string;
  status: string;
  summary_preview: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface QualityEvent {
  task_id: string;
  agent_id: string;
  agent_role?: string;
  grade?: string;
  issues?: string[];
  type?: string;
  contradictions_count?: number;
  consensus_count?: number;
  blind_spots_count?: number;
  timestamp: string;
}

export interface CrossValidationEvent {
  contradictions_count: number;
  consensus_count: number;
  blind_spots_count: number;
  high_severity: number;
  timestamp: string;
}

export interface ChainSwarmDetail {
  chain_id: string;
  run_id: string;
  run_status: string;
  tasks: SwarmTaskSummary[];
  quality: QualityEvent[];
  cross_validation: CrossValidationEvent[];
  final_report_length: number;
  total_tokens: { input: number; output: number };
}

export interface ChainCompareItem {
  chain_id: string;
  name: string;
  status: string;
  lifecycle_stage: string;
  prosperity_score: number | null;
  sector_score: number | null;
  segment_scores: Record<string, number>;
  segment_names: string[];
}

export interface SharedTicker {
  code: string;
  name: string;
  chains: string[];
}

export interface ChainCompareResult {
  chains: ChainCompareItem[];
  shared_tickers: SharedTicker[];
}

export interface ChainHypothesis {
  hypothesis_id: string;
  title: string;
  thesis: string;
  status: string;
  universe: string;
  invalidation_notes: string;
  run_cards: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface CreateHypothesisRequest {
  title: string;
  thesis: string;
  status?: string;
  invalidation_notes?: string;
}
