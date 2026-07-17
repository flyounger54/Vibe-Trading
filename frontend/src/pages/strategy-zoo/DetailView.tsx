import i18n from "@/i18n";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Loader2, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type StrategyDetailResponse } from "@/lib/api";
import { metaString, RiskBadge, CategoryBadge } from "./shared";

export function DetailView({ strategyId }: { strategyId: string }) {
  const [detail, setDetail] = useState<StrategyDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .getStrategy(strategyId)
      .then((res) => { if (alive) setDetail(res); })
      .catch((err: unknown) => {
        if (!alive) return;
        const msg = err instanceof Error ? err.message : "Failed to load strategy";
        setError(msg);
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [strategyId]);

  if (loading) {
    return (
      <div className="p-8 flex items-center justify-center text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin mr-2" aria-hidden="true" /> Loading {strategyId}…
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="p-8 max-w-3xl mx-auto space-y-4">
        <Link to="/strategy-zoo" className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1">
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> {i18n.t("strategyZoo.backToList")}
        </Link>
        <div className="border rounded-xl p-6 bg-card">
          <h2 className="font-semibold text-sm mb-1 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-warning" aria-hidden="true" /> {i18n.t("strategyZoo.loadError")}
          </h2>
          <p className="text-sm text-muted-foreground">{error || "Unknown error"}</p>
        </div>
      </div>
    );
  }

  const s = detail.strategy;
  const meta = s.meta || {};

  return (
    <div className="p-4 md:p-8 max-w-4xl mx-auto space-y-6">
      <Link
        to="/strategy-zoo"
        className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> {i18n.t("strategyZoo.backToList")}
      </Link>

      {/* Title */}
      <div className="space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <h1 className="font-mono text-xl md:text-2xl font-bold tracking-tight">
            {s.id}
          </h1>
          <CategoryBadge category={s.category} />
          <RiskBadge risk={metaString(meta, "risk_profile")} />
        </div>
        {meta["nickname"] ? (
          <p className="text-sm text-muted-foreground">{String(meta["nickname"])}</p>
        ) : null}
        {meta["description"] ? (
          <p className="text-sm text-muted-foreground">{String(meta["description"])}</p>
        ) : null}
      </div>

      {/* Metadata */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">{i18n.t("strategyZoo.metadata")}</h2>
        <div className="border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <tbody>
              <MetaRow label={i18n.t("strategyZoo.category")} value={metaString(meta, "category")} />
              <MetaRow label={i18n.t("strategyZoo.universe")} value={metaString(meta, "universe")} />
              <MetaRow label={i18n.t("strategyZoo.frequency")} value={metaString(meta, "frequency")} />
              <MetaRow label={i18n.t("strategyZoo.risk")} value={metaString(meta, "risk_profile")} />
              <MetaRow label={i18n.t("strategyZoo.minBars")} value={metaString(meta, "min_bars")} />
              <MetaRow label={i18n.t("strategyZoo.columnsRequired")} value={metaString(meta, "columns_required")} />
              <MetaRow label={i18n.t("strategyZoo.defaultParams")} value={metaString(meta, "default_params")} />
              <MetaRow label={i18n.t("strategyZoo.factorsUsed")} value={metaString(meta, "factors_used")} />
              <MetaRow label={i18n.t("strategyZoo.reference")} value={metaString(meta, "reference")} />
              <MetaRow label="Module path" value={s.module_path || "—"} last />
            </tbody>
          </table>
        </div>
      </section>

      {/* Source code */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">{i18n.t("strategyZoo.sourceCode")}</h2>
        <details className="border rounded-xl bg-card group">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium hover:bg-muted/40 select-none">
            {i18n.t("strategyZoo.viewSource")} ({(detail.source_code || "").split("\n").length} lines)
          </summary>
          <pre className="border-t bg-muted/30 p-4 overflow-x-auto text-xs leading-relaxed">
            <code>{detail.source_code || "(no source available)"}</code>
          </pre>
        </details>
      </section>
    </div>
  );
}

export function MetaRow({ label, value, last }: { label: string; value: string; last?: boolean }) {
  return (
    <tr className={cn(!last && "border-b", "hover:bg-muted/20")}>
      <td className="px-4 py-2 text-xs text-muted-foreground w-1/3">{label}</td>
      <td className="px-4 py-2 text-xs font-mono break-all">{value}</td>
    </tr>
  );
}
