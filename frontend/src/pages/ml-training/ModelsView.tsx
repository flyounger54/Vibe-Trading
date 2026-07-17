import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Trash2, AlertTriangle, CheckCircle2, Brain } from "lucide-react";
import { api, type MLModelSummary } from "@/lib/api";
import { ErrorRetryBanner } from "@/components/common/ErrorRetryBanner";
import { ContextTip, LoadingState } from "./shared";

export function ModelsView() {
  const { t } = useTranslation();
  const [models, setModels] = useState<MLModelSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api
      .listMLModels()
      .then((r) => setModels(r.models))
      .catch((e) => setError(e instanceof Error ? e.message : "Error"))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleDelete = async (id: string) => {
    if (!confirm(t("mlTraining.models.confirmDelete", { id }))) return;
    try {
      await api.deleteMLModel(id);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  if (loading) return <><ContextTip tipKey="models" /><LoadingState /></>;
  if (error) return <><ContextTip tipKey="models" /><ErrorRetryBanner message={error} onRetry={load} /></>;

  if (models.length === 0) {
    return (
      <div className="space-y-4">
        <ContextTip tipKey="models" />
        <div className="border rounded-xl p-8 bg-card text-center text-muted-foreground">
          <Brain className="h-8 w-8 mx-auto mb-3 opacity-40" />
          <p className="text-sm">{t("mlTraining.models.empty")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <ContextTip tipKey="models" />
      <div className="border rounded-xl overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm" aria-label="Trained models">
          <thead>
            <tr className="border-b bg-muted/40">
              <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Model ID</th>
              <th className="text-left px-3 py-2.5 text-muted-foreground font-medium hidden md:table-cell">Type</th>
              <th className="text-left px-3 py-2.5 text-muted-foreground font-medium">Universe</th>
              <th className="text-right px-3 py-2.5 text-muted-foreground font-medium">Horizon</th>
              <th className="text-right px-3 py-2.5 text-muted-foreground font-medium">IC</th>
              <th className="text-right px-3 py-2.5 text-muted-foreground font-medium hidden md:table-cell">AUC</th>
              <th className="text-right px-3 py-2.5 text-muted-foreground font-medium hidden lg:table-cell">Features</th>
              <th className="text-center px-3 py-2.5 text-muted-foreground font-medium">Status</th>
              <th className="px-3 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.model_id} className="border-b last:border-0 hover:bg-muted/20 transition">
                <td className="px-4 py-2.5 font-mono text-xs">{m.model_id}</td>
                <td className="px-3 py-2.5 hidden md:table-cell">
                  <span className="px-2 py-0.5 rounded-full bg-muted text-xs">{m.model_type}</span>
                </td>
                <td className="px-3 py-2.5 text-xs">{m.universe}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{m.label_horizon}d</td>
                <td className="px-3 py-2.5 text-right tabular-nums font-medium">
                  {m.cv_ic_mean != null ? m.cv_ic_mean.toFixed(4) : "—"}
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums hidden md:table-cell">
                  {m.cv_auc_mean != null ? m.cv_auc_mean.toFixed(3) : "—"}
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums hidden lg:table-cell">{m.n_features}</td>
                <td className="px-3 py-2.5 text-center">
                  {m.overfit_warning ? (
                    <span className="text-warning text-xs" title="Overfit warning">
                      <AlertTriangle className="h-3.5 w-3.5 inline" />
                    </span>
                  ) : (
                    <span className="text-success">
                      <CheckCircle2 className="h-3.5 w-3.5 inline" />
                    </span>
                  )}
                </td>
                <td className="px-3 py-2.5 text-right">
                  <button
                    onClick={() => handleDelete(m.model_id)}
                    className="text-muted-foreground hover:text-destructive transition p-1"
                    title="Delete"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </div>
    </div>
  );
}

// ===========================================================================
// Train View
// ===========================================================================

