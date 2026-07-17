import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, GitCompare, XCircle, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type MLModelSummary, type MLCompareRow } from "@/lib/api";
import { ContextTip, LoadingState } from "./shared";

export function CompareView() {
  const { t } = useTranslation();
  const [models, setModels] = useState<MLModelSummary[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [comparison, setComparison] = useState<MLCompareRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    api.listMLModels()
      .then((r) => setModels(r.models))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const handleCompare = async () => {
    if (selected.size < 2) return;
    setComparing(true);
    try {
      const r = await api.compareMLModels([...selected]);
      setComparison(r.comparison);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Compare failed");
    } finally {
      setComparing(false);
    }
  };

  if (loading) return <LoadingState />;

  return (
    <div className="space-y-4">
      <ContextTip tipKey="compare" />
      <div className="border rounded-xl p-4 bg-card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium">{t("mlTraining.compare.select")}</h3>
          <button
            onClick={handleCompare}
            disabled={selected.size < 2 || comparing}
            className="inline-flex items-center gap-2 px-4 py-1.5 rounded-lg bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
          >
            {comparing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitCompare className="h-3.5 w-3.5" />}
            {t("mlTraining.compare.run")} ({selected.size})
          </button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
          {models.map((m) => (
            <label
              key={m.model_id}
              className={cn(
                "flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer transition text-sm",
                selected.has(m.model_id) ? "border-primary bg-primary/5" : "hover:bg-muted/30"
              )}
            >
              <input
                type="checkbox"
                checked={selected.has(m.model_id)}
                onChange={() => toggle(m.model_id)}
                className="rounded"
              />
              <span className="font-mono text-xs truncate flex-1">{m.model_id}</span>
              <span className="text-muted-foreground text-xs shrink-0">{m.model_type}</span>
            </label>
          ))}
        </div>
      </div>

      {comparison && (
        <div className="border rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" aria-label="Model comparison">
              <thead>
                <tr className="border-b bg-muted/40">
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Model</th>
                  <th className="text-left px-3 py-2.5 text-muted-foreground font-medium">Type</th>
                  <th className="text-right px-3 py-2.5 text-muted-foreground font-medium">Horizon</th>
                  <th className="text-right px-3 py-2.5 text-muted-foreground font-medium">IC Mean</th>
                  <th className="text-right px-3 py-2.5 text-muted-foreground font-medium">AUC</th>
                  <th className="text-right px-3 py-2.5 text-muted-foreground font-medium">Features</th>
                  <th className="text-center px-3 py-2.5 text-muted-foreground font-medium">Overfit</th>
                </tr>
              </thead>
              <tbody>
                {comparison.map((r) => (
                  <tr key={r.model_id} className="border-b last:border-0 hover:bg-muted/20">
                    <td className="px-4 py-2.5 font-mono text-xs">{r.model_id}</td>
                    <td className="px-3 py-2.5">
                      <span className="px-2 py-0.5 rounded-full bg-muted text-xs">{r.model_type}</span>
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{r.horizon}d</td>
                    <td className="px-3 py-2.5 text-right tabular-nums font-medium">
                      {r.test_ic_mean != null ? r.test_ic_mean.toFixed(4) : "—"}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {r.test_auc_mean != null ? r.test_auc_mean.toFixed(3) : "—"}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{r.n_features}</td>
                    <td className="px-3 py-2.5 text-center">
                      {r.overfit_warning ? (
                        <XCircle className="h-3.5 w-3.5 inline text-warning" />
                      ) : (
                        <CheckCircle2 className="h-3.5 w-3.5 inline text-success" />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// Health View
// ===========================================================================
