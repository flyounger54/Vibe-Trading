import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, AlertTriangle, CheckCircle2, HeartPulse } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { ContextTip, FormField } from "./shared";

export function HealthView() {
  const { t } = useTranslation();
  const [period, setPeriod] = useState("2026-04-01/2026-06-24");
  const [checking, setChecking] = useState(false);
  const [reports, setReports] = useState<Array<{ model_id: string; drift_score: number; retrain_recommended: boolean }> | null>(null);

  const handleCheck = async () => {
    setChecking(true);
    try {
      const r = await api.checkModelHealth({ recent_period: period });
      setReports(r.reports || (r.model_id ? [{ model_id: r.model_id, drift_score: r.drift_score!, retrain_recommended: r.retrain_recommended! }] : []));
    } catch (e) {
      alert(e instanceof Error ? e.message : "Health check failed");
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="space-y-4">
      <ContextTip tipKey="health" />
      <div className="border rounded-xl p-4 bg-card flex flex-col md:flex-row gap-3 md:items-end">
        <FormField label={t("mlTraining.health.period")}>
          <input value={period} onChange={(e) => setPeriod(e.target.value)} className="form-input" placeholder="2026-04-01/2026-06-24" />
        </FormField>
        <button
          onClick={handleCheck}
          disabled={checking}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
        >
          {checking ? <Loader2 className="h-4 w-4 animate-spin" /> : <HeartPulse className="h-4 w-4" />}
          {t("mlTraining.health.check")}
        </button>
      </div>

      {reports && (
        reports.length === 0 ? (
          <div className="border rounded-xl p-6 bg-card text-center text-sm text-muted-foreground">
            {t("mlTraining.health.noModels")}
          </div>
        ) : (
          <div className="border rounded-xl overflow-hidden">
            <table className="w-full text-sm" aria-label="Model health">
              <thead>
                <tr className="border-b bg-muted/40">
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Model</th>
                  <th className="text-right px-3 py-2.5 text-muted-foreground font-medium">Drift Score</th>
                  <th className="text-center px-3 py-2.5 text-muted-foreground font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((r) => (
                  <tr key={r.model_id} className="border-b last:border-0 hover:bg-muted/20">
                    <td className="px-4 py-2.5 font-mono text-xs">{r.model_id}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      <span className={cn(
                        "font-medium",
                        r.drift_score > 0.5 ? "text-destructive" : r.drift_score > 0.3 ? "text-warning" : "text-success"
                      )}>
                        {r.drift_score.toFixed(2)}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      {r.retrain_recommended ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-destructive/10 text-destructive text-xs">
                          <AlertTriangle className="h-3 w-3" />
                          {t("mlTraining.health.retrain")}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-success/10 text-success text-xs">
                          <CheckCircle2 className="h-3 w-3" />
                          {t("mlTraining.health.healthy")}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}

// ===========================================================================
// Guide View
// ===========================================================================
