import i18n from "@/i18n";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { ArrowLeft, ArrowLeftRight, Loader2, CheckCircle2, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { api, type AlphaCompareResult } from "@/lib/api";
import { AuthenticatedEventStream } from "@/lib/fetchSSE";
import { UNIVERSE_OPTIONS, fmtNum } from "./constants";
import { ProgressPanel, type BenchStatus, type BenchProgress } from "./BenchView";

const SORT_OPTIONS = [
  { value: "ir", label: "IR (information ratio)" },
  { value: "ic_mean", label: "IC mean" },
  { value: "ic_positive_ratio", label: "IC > 0 ratio" },
  { value: "ic_count", label: "Sample count" },
];

function parseAlphaIds(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of text.split(/[\s,]+/)) {
    const id = raw.trim();
    if (id && !seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

export function CompareView() {
  const { search: locSearch } = useLocation();
  const initialIds = useMemo(() => {
    const q = new URLSearchParams(locSearch);
    return parseAlphaIds(q.get("ids") || "").join(", ");
  }, [locSearch]);

  const [idsText, setIdsText] = useState(initialIds);
  const [universe, setUniverse] = useState("csi300");
  const [period, setPeriod] = useState("2020-2025");
  const [sort, setSort] = useState("ir");

  const [status, setStatus] = useState<BenchStatus>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState<BenchProgress | null>(null);
  const [result, setResult] = useState<AlphaCompareResult | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const sourceRef = useRef<AuthenticatedEventStream | null>(null);
  const doneRef = useRef(false);

  const ids = useMemo(() => parseAlphaIds(idsText), [idsText]);

  useEffect(() => {
    return () => {
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, []);

  const attachStream = (newJobId: string) => {
    setStatus("streaming");
    const source = new AuthenticatedEventStream(api.alphaCompareStreamUrl(newJobId));
    sourceRef.current = source;

    source.addEventListener("progress", (e) => {
      try {
        setProgress(JSON.parse((e as MessageEvent).data) as BenchProgress);
      } catch {
        /* ignore */
      }
    });
    source.addEventListener("result", (e) => {
      try {
        setResult(JSON.parse((e as MessageEvent).data) as AlphaCompareResult);
      } catch {
        /* ignore */
      }
    });
    source.addEventListener("done", () => {
      doneRef.current = true;
      setStatus("done");
      source.close();
      sourceRef.current = null;
    });
    source.addEventListener("error", (e) => {
      // The stream transport reports the close that follows `done`;
      // the ref check (synchronous) is the only reliable race guard.
      if (doneRef.current) {
        source.close();
        sourceRef.current = null;
        return;
      }
      let msg = "Compare stream error";
      try {
        const data = JSON.parse((e as MessageEvent).data || "{}");
        if (typeof data.message === "string") msg = data.message;
      } catch {
        /* network-level error, no payload */
      }
      toast.error(msg);
      setStatus("error");
      source.close();
      sourceRef.current = null;
    });
  };

  const startCompare = async (e: FormEvent) => {
    e.preventDefault();
    if (status === "submitting" || status === "streaming") return;
    if (ids.length < 2) {
      setFormError("Enter at least 2 distinct alpha ids to compare.");
      return;
    }
    setStatus("submitting");
    setProgress(null);
    setResult(null);
    setFormError(null);
    doneRef.current = false;
    sourceRef.current?.close();
    try {
      const res = await api.createAlphaCompare({
        alpha_ids: ids,
        universe,
        period,
        sort,
      });
      setJobId(res.job_id);
      attachStream(res.job_id);
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : "Failed to start comparison";
      toast.error(msg);
      setStatus("error");
    }
  };

  const busy = status === "submitting" || status === "streaming";

  return (
    <div className="p-4 md:p-8 max-w-5xl mx-auto space-y-6">
      <Link
        to="/alpha-zoo"
        className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back to Alpha Zoo
      </Link>

      <div className="space-y-1">
        <div className="flex items-center gap-2 text-xs text-muted-foreground uppercase tracking-wide">
          <ArrowLeftRight className="h-3.5 w-3.5" aria-hidden="true" /> Head-to-head compare
        </div>
        <h1 className="text-2xl md:text-3xl font-bold tracking-tight">
          Compare alphas side by side
        </h1>
        <p className="text-sm text-muted-foreground max-w-2xl">
          Benches just the alphas you pick on a universe and period, then ranks
          them by IC / IR with the gap to the leader — far faster than benching a
          whole zoo when you only care about a shortlist.
        </p>
      </div>

      <form onSubmit={startCompare} className="border rounded-xl p-4 bg-card space-y-3">
        <div>
          <label htmlFor="compare-ids" className="text-xs text-muted-foreground block mb-1">
            Alpha ids{ids.length > 0 ? ` (${ids.length} selected)` : ""}
          </label>
          <textarea
            id="compare-ids"
            value={idsText}
            onChange={(e) => setIdsText(e.target.value)}
            disabled={busy}
            rows={2}
            placeholder="alpha101_1, alpha101_2, gtja191_5"
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
          />
          <p className="text-[11px] text-muted-foreground mt-1">
            Separate ids with commas or spaces. Tip: tick alphas in the catalogue
            and hit “Compare” to prefill this.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label htmlFor="compare-universe" className="text-xs text-muted-foreground block mb-1">Universe</label>
            <select
              id="compare-universe"
              value={universe}
              onChange={(e) => setUniverse(e.target.value)}
              disabled={busy}
              className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
            >
              {UNIVERSE_OPTIONS.map((u) => (
                <option key={u.value} value={u.value}>
                  {u.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="compare-period" className="text-xs text-muted-foreground block mb-1">Period</label>
            <input
              id="compare-period"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              disabled={busy}
              placeholder="2020-2025"
              className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
            />
          </div>
          <div>
            <label htmlFor="compare-sort" className="text-xs text-muted-foreground block mb-1">{i18n.t("alphaZoo.rankBy")}</label>
            <select
              id="compare-sort"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              disabled={busy}
              className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
            >
              {SORT_OPTIONS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={busy || ids.length < 2}
            className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {busy ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> Running…
              </>
            ) : (
              <>
                <ArrowLeftRight className="h-3.5 w-3.5" aria-hidden="true" /> Compare
              </>
            )}
          </button>
          {ids.length < 2 && (
            <span className="text-xs text-muted-foreground">{i18n.t("alphaZoo.pickAtLeast2")}</span>
          )}
        </div>

        {formError && (
          <p className="text-xs text-red-600 dark:text-red-400" role="alert">
            {formError}
          </p>
        )}
      </form>

      {(status === "submitting" || status === "streaming") && (
        <ProgressPanel jobId={jobId} progress={progress} />
      )}

      {result && <CompareResultPanel result={result} />}
    </div>
  );
}

export function CompareResultPanel({ result }: { result: AlphaCompareResult }) {
  const deltaKey = `delta_${result.sort}_vs_best`;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <span className="inline-flex items-center gap-1.5 font-medium text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> Winner:{" "}
          <span className="font-mono">{result.winner}</span>
        </span>
        <span className="text-muted-foreground">
          {result.n_compared} compared · ranked by {result.sort} · {result.universe} · {result.period}
        </span>
        {result.n_skipped > 0 && (
          <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" /> {result.n_skipped} skipped
          </span>
        )}
      </div>

      <div className="border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="Alpha comparison ranking">
            <thead>
              <tr className="border-b bg-muted/40 text-muted-foreground text-xs">
                <th className="text-right px-3 py-2">#</th>
                <th className="text-left px-3 py-2">Alpha</th>
                <th className="text-left px-3 py-2 hidden sm:table-cell">Zoo</th>
                <th className="text-right px-3 py-2">IC mean</th>
                <th className="text-right px-3 py-2 hidden md:table-cell">IC std</th>
                <th className="text-right px-3 py-2">IR</th>
                <th className="text-right px-3 py-2 hidden md:table-cell" title="Share of periods with positive IC">IC&gt;0</th>
                <th className="text-right px-3 py-2 hidden lg:table-cell" title="IC sample count">n</th>
                <th className="text-right px-3 py-2" title={`Gap to the leader on ${result.sort}`}>Δ {result.sort}</th>
              </tr>
            </thead>
            <tbody>
              {result.ranking.map((r) => (
                <tr
                  key={`${r.zoo}:${r.id}`}
                  className={cn(
                    "border-b last:border-0 hover:bg-muted/20",
                    r.rank === 1 && "bg-emerald-500/5",
                  )}
                >
                  <td className="px-3 py-2 text-right font-mono tabular-nums">{r.rank}</td>
                  <td className="px-3 py-2 font-mono text-xs">
                    <Link
                      to={`/alpha-zoo/${encodeURIComponent(r.id)}`}
                      className="text-primary hover:underline"
                    >
                      {r.id}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground hidden sm:table-cell">{r.zoo}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">{fmtNum(r.ic_mean, 4)}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums hidden md:table-cell">{fmtNum(r.ic_std, 4)}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">{fmtNum(r.ir, 3)}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums hidden md:table-cell">{fmtNum(r.ic_positive_ratio, 3)}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums hidden lg:table-cell">{r.ic_count}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-muted-foreground">
                    {r.rank === 1 ? "—" : fmtNum(Number(r[deltaKey]), 4)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {result.skipped.length > 0 && (
        <p className="text-xs text-muted-foreground">
          <span className="font-medium">Skipped:</span>{" "}
          {result.skipped.map((s) => `${s.id} (${s.reason})`).join("; ")}
        </p>
      )}
    </div>
  );
}
