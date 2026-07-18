import i18n from '@/i18n';
import { useState } from "react";
import { BarChart3 } from "lucide-react";
import { CorrelationMatrix } from "@/components/charts/CorrelationMatrix";
import { api } from "@/lib/api";

const WINDOWS = [30, 60, 90, 180, 365] as const;

export function Correlation() {
  const [codes, setCodes] = useState("BTC-USDT,ETH-USDT,SPY,AAPL");
  const [days, setDays] = useState<number>(90);
  const [method, setMethod] = useState<"pearson" | "spearman">("pearson");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [labels, setLabels] = useState<string[]>([]);
  const [matrix, setMatrix] = useState<number[][]>([]);

  const compute = async () => {
    setError(null);
    setLoading(true);
    try {
      const result = await api.getCorrelation(codes, days, method);
      setLabels(result.labels);
      setMatrix(result.matrix);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to compute correlation");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 p-4 md:p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <BarChart3 className="h-6 w-6 text-primary" />
        <h1 className="text-2xl font-bold">{i18n.t("correlation.title")}</h1>
      </div>

      {/* Controls */}
      <div className="flex flex-col gap-4 border rounded-lg p-4">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="correlation-codes" className="text-sm font-medium">{i18n.t("correlation.assetCodes")}</label>
          <input
            id="correlation-codes"
            type="text"
            value={codes}
            onChange={(e) => setCodes(e.target.value)}
            placeholder="BTC-USDT,ETH-USDT,SPY"
            className="w-full px-3 py-2 rounded-md border bg-background text-sm"
          />
          <p className="text-xs text-muted-foreground">
            Comma-separated ticker symbols, e.g. BTC-USDT,ETH-USDT,AAPL,SPY
          </p>
        </div>

        <div className="flex flex-wrap gap-4">
          <fieldset className="flex flex-col gap-1.5">
            <legend className="text-sm font-medium">{i18n.t("correlation.windowDays")}</legend>
            <div className="flex flex-wrap gap-1.5">
              {WINDOWS.map((w) => (
                <button
                  type="button"
                  key={w}
                  onClick={() => setDays(w)}
                  aria-pressed={days === w}
                  className={`px-3 py-1.5 rounded text-sm border transition-colors ${
                    days === w
                      ? "bg-primary text-primary-foreground"
                      : "border-muted-foreground/30 hover:border-primary"
                  }`}
                >
                  {w}d
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-1.5">
            <legend className="text-sm font-medium">{i18n.t("correlation.method")}</legend>
            <div className="flex gap-1.5">
              {(["pearson", "spearman"] as const).map((m) => (
                <button
                  type="button"
                  key={m}
                  onClick={() => setMethod(m)}
                  aria-pressed={method === m}
                  className={`px-3 py-1.5 rounded text-sm border transition-colors capitalize ${
                    method === m
                      ? "bg-primary text-primary-foreground"
                      : "border-muted-foreground/30 hover:border-primary"
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          </fieldset>
        </div>

        <button
          type="button"
          onClick={compute}
          disabled={loading}
          className="self-start px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {loading ? i18n.t("correlation.loading") : i18n.t("correlation.compute")}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="text-sm text-danger border border-danger/30 rounded p-3 bg-danger/5" role="alert">
          {error}
        </div>
      )}

      {/* Chart */}
      {labels.length > 0 && <CorrelationMatrix labels={labels} matrix={matrix} height={520} />}
    </div>
  );
}
