import i18n from "@/i18n";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Target, Search, Loader2, Library } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type StrategySummary } from "@/lib/api";
import { CATEGORY_CARDS, UNIVERSE_OPTIONS, RISK_OPTIONS, PAGE_SIZE, RiskBadge, CategoryBadge } from "./shared";
import { ErrorRetryBanner } from "@/components/common/ErrorRetryBanner";

export function BrowseView() {
  const [strategies, setStrategies] = useState<StrategySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [categoryFilter, setCategoryFilter] = useState<string>("");
  const [universeFilter, setUniverseFilter] = useState<string>("");
  const [riskFilter, setRiskFilter] = useState<string>("");
  const [search, setSearch] = useState("");
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [total, setTotal] = useState<number>(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setLoadError(null);
    api
      .listStrategies({
        category: categoryFilter || undefined,
        universe: universeFilter || undefined,
        risk: riskFilter || undefined,
        limit: 1000,
      })
      .then((res) => {
        if (!alive) return;
        setStrategies(res.strategies);
        setTotal(res.total);
        setVisibleCount(PAGE_SIZE);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        const msg = err instanceof Error ? err.message : "Failed to load strategies";
        setLoadError(msg);
        setStrategies([]);
        setTotal(0);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => { alive = false; };
  }, [categoryFilter, universeFilter, riskFilter, retryKey]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return strategies;
    return strategies.filter(
      (s) =>
        s.id.toLowerCase().includes(q) ||
        s.nickname.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q),
    );
  }, [strategies, search]);

  const visible = filtered.slice(0, visibleCount);

  const catCounts = useMemo(() => {
    const m: Record<string, number> = {};
    for (const s of strategies) m[s.category] = (m[s.category] || 0) + 1;
    return m;
  }, [strategies]);

  return (
    <div className="p-4 md:p-8 max-w-6xl mx-auto space-y-8">
      {/* Hero */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-xs text-muted-foreground uppercase tracking-wide">
          <Target className="h-3.5 w-3.5" aria-hidden="true" /> Strategy Zoo
        </div>
        <h1 className="text-2xl md:text-3xl font-bold tracking-tight">
          {loading ? i18n.t("strategyZoo.loading") : i18n.t("strategyZoo.prebuiltStrategy", { count: total })}
        </h1>
        <p className="text-sm text-muted-foreground max-w-2xl">
          {i18n.t("strategyZoo.heroDescription")}
        </p>
      </div>

      {loadError ? <ErrorRetryBanner message={loadError} onRetry={() => setRetryKey((key) => key + 1)} /> : null}

      {/* Category cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        {CATEGORY_CARDS.map((c) => {
          const active = categoryFilter === c.id;
          const count = catCounts[c.id] || 0;
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => setCategoryFilter(active ? "" : c.id)}
              className={cn(
                "text-left border rounded-xl p-3 space-y-1.5 transition bg-gradient-to-br",
                c.accent,
                "hover:border-primary/50",
                active && "border-primary ring-1 ring-primary/30",
              )}
            >
              <div className="flex items-center justify-between">
                <Library className="h-4 w-4 text-primary" aria-hidden="true" />
                <span className="text-xs font-mono text-muted-foreground">
                  {count}
                </span>
              </div>
              <h3 className="font-semibold text-xs leading-tight">{c.titleZh}</h3>
              <p className="text-[10px] text-muted-foreground line-clamp-2">
                {c.description}
              </p>
            </button>
          );
        })}
      </div>

      {/* Filter bar */}
      <div className="flex flex-col md:flex-row md:items-end gap-3 border rounded-xl p-4 bg-card">
        <div className="flex-1 min-w-0">
          <label htmlFor="strategy-search" className="text-xs text-muted-foreground block mb-1">
            {i18n.t("strategyZoo.search")}
          </label>
          <div className="relative">
            <Search
              className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground"
              aria-hidden="true"
            />
            <input
              id="strategy-search"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setVisibleCount(PAGE_SIZE);
              }}
              placeholder={i18n.t("strategyZoo.searchPlaceholder")}
              className="w-full pl-9 pr-3 py-2 rounded-lg border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
          </div>
        </div>
        <div className="md:w-40">
          <label htmlFor="strategy-cat-filter" className="text-xs text-muted-foreground block mb-1">
            {i18n.t("strategyZoo.category")}
          </label>
          <select
            id="strategy-cat-filter"
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <option value="">{i18n.t("strategyZoo.allCategories")}</option>
            {CATEGORY_CARDS.map((c) => (
              <option key={c.id} value={c.id}>{c.titleZh}</option>
            ))}
          </select>
        </div>
        <div className="md:w-40">
          <label htmlFor="strategy-universe-filter" className="text-xs text-muted-foreground block mb-1">
            {i18n.t("strategyZoo.universe")}
          </label>
          <select
            id="strategy-universe-filter"
            value={universeFilter}
            onChange={(e) => setUniverseFilter(e.target.value)}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <option value="">{i18n.t("strategyZoo.allUniverses")}</option>
            {UNIVERSE_OPTIONS.map((u) => (
              <option key={u.value} value={u.value}>{u.label}</option>
            ))}
          </select>
        </div>
        <div className="md:w-36">
          <label htmlFor="strategy-risk-filter" className="text-xs text-muted-foreground block mb-1">
            {i18n.t("strategyZoo.risk")}
          </label>
          <select
            id="strategy-risk-filter"
            value={riskFilter}
            onChange={(e) => setRiskFilter(e.target.value)}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <option value="">{i18n.t("strategyZoo.allRisks")}</option>
            {RISK_OPTIONS.map((r) => (
              <option key={r.value} value={r.value}>{r.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="Strategy catalogue">
            <caption className="sr-only">Strategy catalogue</caption>
            <thead>
              <tr className="border-b bg-muted/40">
                <th className="text-left px-4 py-2.5 text-muted-foreground">ID</th>
                <th className="text-left px-4 py-2.5 text-muted-foreground">
                  {i18n.t("strategyZoo.category")}
                </th>
                <th className="text-left px-4 py-2.5 text-muted-foreground hidden md:table-cell">
                  {i18n.t("strategyZoo.universe")}
                </th>
                <th className="text-left px-4 py-2.5 text-muted-foreground">
                  {i18n.t("strategyZoo.risk")}
                </th>
                <th className="text-left px-4 py-2.5 text-muted-foreground hidden lg:table-cell">
                  {i18n.t("strategyZoo.reference")}
                </th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin inline mr-2" aria-hidden="true" />
                    {i18n.t("strategyZoo.loading")}
                  </td>
                </tr>
              ) : visible.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
                    {i18n.t("strategyZoo.noResults")}
                  </td>
                </tr>
              ) : (
                visible.map((s) => (
                  <tr
                    key={s.id}
                    className="border-b last:border-0 hover:bg-muted/20"
                  >
                    <td className="px-4 py-2 font-mono text-xs">
                      <Link
                        to={`/strategy-zoo/${encodeURIComponent(s.id)}`}
                        className="text-primary hover:underline"
                      >
                        {s.id}
                      </Link>
                      {s.nickname && (
                        <span className="ml-2 text-muted-foreground font-sans">
                          {s.nickname}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-xs">
                      <CategoryBadge category={s.category} />
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground hidden md:table-cell">
                      {s.universe.join(", ") || "—"}
                    </td>
                    <td className="px-4 py-2 text-xs">
                      <RiskBadge risk={s.risk_profile} />
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground hidden lg:table-cell truncate max-w-[200px]">
                      {s.reference || "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {!loading && visible.length < filtered.length && (
          <div className="border-t p-3 flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {i18n.t("strategyZoo.showing")} {visible.length} / {filtered.length}
            </span>
            <button
              type="button"
              onClick={() => setVisibleCount((c) => c + PAGE_SIZE)}
              className="px-3 py-1 rounded-md border hover:bg-muted hover:text-foreground transition"
            >
              {i18n.t("strategyZoo.loadMore")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- Detail view ---------- */
