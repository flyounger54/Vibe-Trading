import i18n from '@/i18n';
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  Code2,
  Database,
  Download,
  FileCheck2,
  Fingerprint,
  List,
  Scale,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type RunCard, type RunData } from "@/lib/api";
import { MetricsCard } from "@/components/chat/MetricsCard";
import { ValidationPanel } from "@/components/charts/ValidationPanel";
import { Skeleton, SkeletonMetrics, SkeletonChart } from "@/components/common/Skeleton";
import { ErrorBoundary } from "@/components/common/ErrorBoundary";
import { ErrorRetryBanner } from "@/components/common/ErrorRetryBanner";
import { cacheFromRun, downloadCsv, buildMetricsCsv, buildTradesCsv, yieldToBrowser, type ChartCache } from "./run-detail/helpers";
import { ChartTab, PositionSizingGuide } from "./run-detail/ChartTab";
import { TradesTab } from "./run-detail/TradesTab";
import { CodeTab } from "./run-detail/CodeTab";

type Tab = "chart" | "trades" | "runCard" | "code" | "validation";
type ChartLoadProgress = { done: number; total: number };


export function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const [run, setRun] = useState<RunData | null>(null);
  const [code, setCode] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<Tab>("chart");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [guideOpen, setGuideOpen] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState("");
  const [chartPickerSymbol, setChartPickerSymbol] = useState("");
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [chartCache, setChartCache] = useState<ChartCache>({});
  const [chartLoadingSymbols, setChartLoadingSymbols] = useState<Record<string, boolean>>({});
  const [bulkChartLoading, setBulkChartLoading] = useState(false);
  const [bulkChartProgress, setBulkChartProgress] = useState<ChartLoadProgress>({ done: 0, total: 0 });
  const chartCacheRef = useRef<ChartCache>({});
  const cancelBulkChartLoadRef = useRef(false);

  const hasValidation = !!run?.validation;
  const hasRunCard = !!run?.run_card;
  const TABS: { id: Tab; label: string; icon: typeof BarChart3; hidden?: boolean }[] = [
    { id: "chart", label: i18n.t("runDetail.chart"), icon: BarChart3 },
    { id: "trades", label: i18n.t("runDetail.trades"), icon: List },
    { id: "validation", label: i18n.t("runDetail.validation"), icon: ShieldCheck, hidden: !hasValidation },
    { id: "runCard", label: i18n.t("runDetail.runCard"), icon: FileCheck2, hidden: !hasRunCard },
    { id: "code", label: i18n.t("runDetail.code"), icon: Code2 },
  ];

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    setLoadError(null);
    Promise.all([
      api.getRun(runId, { chart_payload: "summary" }),
      api.getRunCode(runId).catch(() => ({})),
    ]).then(([r, c]) => {
      setRun(r);
      setCode(c || {});
      const firstSymbol = r?.chart_symbols?.[0] || Object.keys(r?.price_series || {})[0] || "";
      setSelectedSymbol(firstSymbol);
      setChartPickerSymbol(firstSymbol);
      setSelectedSymbols(firstSymbol ? [firstSymbol] : []);
      const initialCache = cacheFromRun(r, firstSymbol);
      chartCacheRef.current = initialCache;
      setChartCache(initialCache);
      if (firstSymbol && !initialCache[firstSymbol]?.price_series?.[firstSymbol]?.length) {
        void loadChartSymbol(firstSymbol);
      }
    }).catch((err) => {
      setLoadError(err instanceof Error ? err.message : "Failed to load run");
    }).finally(() => setLoading(false));
  }, [runId, retryKey]);

  if (loading) {
    return (
      <div className="p-8 space-y-4">
        <Skeleton className="h-6 w-48" />
        <SkeletonMetrics />
        <SkeletonChart height={400} />
      </div>
    );
  }
  if (loadError) return (
    <div className="p-8">
      <ErrorRetryBanner message={loadError} onRetry={() => setRetryKey((k) => k + 1)} />
    </div>
  );
  if (!run) return (
    <div className="p-8 space-y-2">
      <p className="text-red-500 font-medium">{i18n.t("runDetail.runNotFound")}</p>
      <p className="text-sm text-muted-foreground">
        {i18n.t("runDetail.runNotFoundDesc")}, or your browser may not have API access configured.
        Check that the API authentication key is set in Settings if accessing remotely.
      </p>
      <button
        onClick={() => navigate(-1)}
        className="text-sm text-primary hover:underline inline-flex items-center gap-1.5"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> {i18n.t("runDetail.goBack")}
      </button>
    </div>
  );

  const ok = run.status === "success";

  async function loadChartSymbol(symbol: string) {
    if (!runId || !symbol) return;
    if (chartCacheRef.current[symbol]?.price_series?.[symbol]?.length) return;
    setChartLoadingSymbols((prev) => ({ ...prev, [symbol]: true }));
    try {
      const nextRun = await api.getRun(runId, { chart_symbol: symbol });
      const nextCache = cacheFromRun(nextRun, symbol);
      const mergedCache = { ...chartCacheRef.current, ...nextCache };
      chartCacheRef.current = mergedCache;
      setChartCache(mergedCache);
      setRun((prev) => prev ? {
        ...prev,
        chart_symbols: nextRun.chart_symbols?.length ? nextRun.chart_symbols : prev.chart_symbols,
        equity_curve: nextRun.equity_curve?.length ? nextRun.equity_curve : prev.equity_curve,
        trade_log: nextRun.trade_log?.length ? nextRun.trade_log : prev.trade_log,
      } : nextRun);
    } finally {
      setChartLoadingSymbols((prev) => {
        const next = { ...prev };
        delete next[symbol];
        return next;
      });
    }
  }

  async function handleAddChartSymbol(symbol: string) {
    if (!symbol) return;
    setSelectedSymbol(symbol);
    setChartPickerSymbol(symbol);
    setSelectedSymbols((prev) => prev.includes(symbol) ? prev : [...prev, symbol]);
    await loadChartSymbol(symbol);
  }

  async function handleCurrentChartOnly(symbol: string) {
    if (!symbol) return;
    setSelectedSymbol(symbol);
    setChartPickerSymbol(symbol);
    setSelectedSymbols([symbol]);
    await loadChartSymbol(symbol);
  }

  function handleRemoveChartSymbol(symbol: string) {
    const nextSymbols = selectedSymbols.filter((item) => item !== symbol);
    setSelectedSymbols(nextSymbols);
    if (selectedSymbol === symbol) {
      const fallback = nextSymbols[0] || run?.chart_symbols?.[0] || "";
      setSelectedSymbol(fallback);
      setChartPickerSymbol(fallback);
    }
  }

  async function handleLoadAllChartSymbols() {
    const symbols = run?.chart_symbols || [];
    if (symbols.length === 0 || bulkChartLoading) return;
    cancelBulkChartLoadRef.current = false;
    setBulkChartLoading(true);
    setBulkChartProgress({ done: 0, total: symbols.length });
    try {
      for (let index = 0; index < symbols.length; index += 1) {
        if (cancelBulkChartLoadRef.current) break;
        const symbol = symbols[index];
        setSelectedSymbol(symbol);
        setChartPickerSymbol(symbol);
        setSelectedSymbols((prev) => prev.includes(symbol) ? prev : [...prev, symbol]);
        await loadChartSymbol(symbol);
        setBulkChartProgress({ done: index + 1, total: symbols.length });
        await yieldToBrowser();
      }
    } finally {
      setBulkChartLoading(false);
    }
  }

  function handleCancelLoadAllCharts() {
    cancelBulkChartLoadRef.current = true;
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="border-b p-4 space-y-3">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate(-1)}
            className="p-1 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            title={i18n.t("runDetail.goBack")}
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          {ok ? <CheckCircle2 className="h-5 w-5 text-success" /> : <XCircle className="h-5 w-5 text-danger" />}
          <h1 className="font-mono text-sm font-medium">{runId}</h1>
          {run.elapsed_seconds && <span className="text-xs text-muted-foreground">{run.elapsed_seconds.toFixed(1)}s</span>}
        </div>
        {run.prompt && <p className="text-sm text-muted-foreground">{run.prompt}</p>}
        {run.metrics && <MetricsCard metrics={run.metrics as Record<string, number>} />}

        <div className="flex items-center gap-1">
          {TABS.filter(t => !t.hidden).map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors",
                tab === id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"
              )}
            >
              <Icon className="h-3.5 w-3.5" /> {label}
            </button>
          ))}

          <div className="ml-auto flex gap-1">
            {run.trade_log && run.trade_log.length > 0 && (
              <button
                onClick={() => downloadCsv(`trades_${runId}.csv`, buildTradesCsv(run.trade_log!))}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs text-muted-foreground hover:bg-muted transition-colors"
                title={i18n.t("runDetail.downloadTradesCsv")}
              >
                <Download className="h-3.5 w-3.5" /> Download Trades CSV
              </button>
            )}
            {run.metrics && (
              <button
                onClick={() => downloadCsv(`metrics_${runId}.csv`, buildMetricsCsv(run.metrics!))}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs text-muted-foreground hover:bg-muted transition-colors"
                title={i18n.t("runDetail.downloadMetricsCsv")}
              >
                <Download className="h-3.5 w-3.5" /> Download Metrics CSV
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        <ErrorBoundary>
          {tab === "chart" && (
            <ChartTab
              run={run}
              chartPickerSymbol={chartPickerSymbol}
              selectedSymbols={selectedSymbols}
              chartCache={chartCache}
              loadingSymbols={chartLoadingSymbols}
              bulkLoading={bulkChartLoading}
              bulkProgress={bulkChartProgress}
              onPickSymbol={setChartPickerSymbol}
              onAddSymbol={handleAddChartSymbol}
              onCurrentOnly={handleCurrentChartOnly}
              onRemoveSymbol={handleRemoveChartSymbol}
              onLoadAll={handleLoadAllChartSymbols}
              onCancelLoadAll={handleCancelLoadAllCharts}
            />
          )}
          {tab === "trades" && <TradesTab run={run} />}
          {tab === "validation" && run.validation && <ValidationPanel data={run.validation} />}
          {tab === "runCard" && run.run_card && <RunCardTab card={run.run_card} />}
          {tab === "code" && <CodeTab code={code} />}
        </ErrorBoundary>
      </div>

      {/* Floating guide button */}
      <button
        type="button"
        onClick={() => setGuideOpen(true)}
        className={cn(
          "fixed bottom-6 right-6 z-30 flex items-center gap-2 px-4 py-2.5 rounded-full",
          "bg-primary text-primary-foreground shadow-lg",
          "hover:opacity-90 transition-all hover:shadow-xl",
          "text-sm font-medium",
          guideOpen && "opacity-0 pointer-events-none",
        )}
        title="打开仓位管理指南"
      >
        <Scale className="h-4 w-4" />
        仓位管理指南
      </button>

      <PositionSizingGuide open={guideOpen} onClose={() => setGuideOpen(false)} />
    </div>
  );
}

function RunCardTab({ card }: { card: RunCard }) {
  const backtest = card.backtest || {};
  const reproducibility = card.reproducibility || {};
  const metrics = card.metrics || {};
  const artifacts = card.artifacts || [];
  const warnings = card.warnings || [];
  const dataSources = card.data_sources || [];

  return (
    <div className="p-4 space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        <RunCardStat label={i18n.t("runDetail.schema")} value={card.schema_version || "unknown"} />
        <RunCardStat label={i18n.t("runDetail.generated")} value={formatRunCardValue(card.generated_at)} />
        <RunCardStat label={i18n.t("runDetail.dataSources")} value={dataSources.length ? dataSources.join(", ") : "None recorded"} />
        <RunCardStat label={i18n.t("runDetail.warnings")} value={String(warnings.length)} tone={warnings.length ? "warning" : "normal"} />
      </div>

      {warnings.length > 0 && (
        <section className="rounded-md border border-amber-500/25 bg-amber-500/5 p-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium text-amber-700 dark:text-amber-300">
            <AlertTriangle className="h-4 w-4" />
            Warnings
          </div>
          <ul className="space-y-1 text-xs text-muted-foreground">
            {warnings.map((warning, index) => <li key={index}>{warning}</li>)}
          </ul>
        </section>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <RunCardPanel title={i18n.t("runDetail.backtestSummary")} icon={Database}>
          <KeyValueTable data={backtest} empty={i18n.t("runDetail.noBacktestSummary")} />
        </RunCardPanel>
        <RunCardPanel title={i18n.t("runDetail.reproducibility")} icon={Fingerprint}>
          <KeyValueTable data={reproducibility} empty={i18n.t("runDetail.noReproducibilityHashes")} monospaceValues />
        </RunCardPanel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <RunCardPanel title={i18n.t("runDetail.metrics")} icon={BarChart3}>
          <KeyValueTable data={metrics} empty={i18n.t("runDetail.noScalarMetrics")} />
        </RunCardPanel>
        <RunCardPanel title={i18n.t("runDetail.validationPayload")} icon={ShieldCheck}>
          {card.validation ? (
            <pre className="max-h-80 overflow-auto rounded-md bg-muted/40 p-3 text-xs leading-relaxed">
              {JSON.stringify(card.validation, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground">{i18n.t("runDetail.noValidationPayload")}</p>
          )}
        </RunCardPanel>
      </div>

      <RunCardPanel title={i18n.t("runDetail.artifactChecksums")} icon={FileCheck2}>
        {artifacts.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-2 pr-4">{i18n.t("runDetail.path")}</th>
                  <th className="py-2 pr-4">{i18n.t("runDetail.size")}</th>
                  <th className="py-2">{i18n.t("runDetail.sha256")}</th>
                </tr>
              </thead>
              <tbody>
                {artifacts.map((artifact) => (
                  <tr key={`${artifact.path}-${artifact.sha256}`} className="border-b last:border-0">
                    <td className="py-2 pr-4 font-mono text-xs">{artifact.path}</td>
                    <td className="py-2 pr-4 tabular-nums text-muted-foreground">{formatBytes(artifact.size_bytes)}</td>
                    <td className="py-2 font-mono text-xs text-muted-foreground">{shortHash(artifact.sha256)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{i18n.t("runDetail.noArtifactChecksums")}</p>
        )}
      </RunCardPanel>
    </div>
  );
}

function RunCardStat({ label, value, tone = "normal" }: { label: string; value: string; tone?: "normal" | "warning" }) {
  return (
    <div className="rounded-md border bg-card p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("mt-1 truncate text-sm font-medium", tone === "warning" ? "text-amber-700 dark:text-amber-300" : "")}>{value}</div>
    </div>
  );
}

function RunCardPanel({ title, icon: Icon, children }: { title: string; icon: typeof FileCheck2; children: ReactNode }) {
  return (
    <section className="rounded-md border bg-card p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium">
        <Icon className="h-4 w-4 text-muted-foreground" />
        {title}
      </div>
      {children}
    </section>
  );
}

function KeyValueTable({ data, empty, monospaceValues = false }: { data: Record<string, unknown>; empty: string; monospaceValues?: boolean }) {
  const entries = Object.entries(data).filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">{empty}</p>;
  }
  return (
    <table className="w-full table-fixed text-sm">
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key} className="border-b last:border-0">
            <td className="w-36 py-2 pr-4 align-top text-muted-foreground">{key}</td>
            <td className={cn("py-2 align-top", monospaceValues ? "break-all font-mono text-xs" : "break-words text-right tabular-nums")}>{formatRunCardValue(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function formatRunCardValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(4);
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return String(value ?? "");
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value)) return "-";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function shortHash(value: string): string {
  return value.length > 16 ? `${value.slice(0, 12)}...${value.slice(-6)}` : value;
}
