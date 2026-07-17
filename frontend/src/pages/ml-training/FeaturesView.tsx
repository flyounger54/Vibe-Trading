import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus, CheckCircle2 } from "lucide-react";
import { api, type MLProfileSummary } from "@/lib/api";
import { ContextTip, LoadingState } from "./shared";

export function FeaturesView() {
  const { t } = useTranslation();
  const [profiles, setProfiles] = useState<MLProfileSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ universe: "csi300", period: "2018-2020", zoo: "qlib158" });
  const [result, setResult] = useState<{ profile_id: string; n_selected: number; factors: string[] } | null>(null);

  useEffect(() => {
    api.listFeatureProfiles()
      .then((r) => setProfiles(r.profiles))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [result]);

  const handleCreate = async () => {
    setCreating(true);
    setResult(null);
    try {
      const r = await api.createFeatureProfile({ ...form, methods: ["ic_filter", "corr_dedup"] });
      setResult(r);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-4">
      <ContextTip tipKey="features" />
      {/* Create form */}
      <div className="border rounded-xl p-4 bg-card">
        <h3 className="text-sm font-medium mb-3">{t("mlTraining.features.create")}</h3>
        <div className="flex flex-col md:flex-row gap-3">
          <select value={form.universe} onChange={(e) => setForm((f) => ({ ...f, universe: e.target.value }))} className="form-select md:w-36">
            <option value="csi300">CSI 300</option>
            <option value="sp500">S&P 500</option>
          </select>
          <input value={form.period} onChange={(e) => setForm((f) => ({ ...f, period: e.target.value }))} className="form-input md:w-44" placeholder="2018-2020" />
          <select value={form.zoo} onChange={(e) => setForm((f) => ({ ...f, zoo: e.target.value }))} className="form-select md:w-36">
            <option value="qlib158">Qlib 158</option>
            <option value="alpha101">Alpha 101</option>
            <option value="gtja191">GTJA 191</option>
            <option value="academic">Academic</option>
          </select>
          <button onClick={handleCreate} disabled={creating} className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50">
            {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            {t("mlTraining.features.run")}
          </button>
        </div>
      </div>

      {/* Result */}
      {result && (
        <div className="border border-success/30 rounded-xl p-4 bg-success/5">
          <p className="text-sm font-medium flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-success" />
            {t("mlTraining.features.created", { id: result.profile_id, n: result.n_selected })}
          </p>
          <div className="mt-2 flex flex-wrap gap-1">
            {result.factors.map((f) => (
              <span key={f} className="px-2 py-0.5 rounded-full bg-muted text-xs font-mono">{f}</span>
            ))}
          </div>
        </div>
      )}

      {/* Existing profiles */}
      {loading ? (
        <LoadingState />
      ) : profiles.length === 0 ? (
        <div className="border rounded-xl p-6 bg-card text-center text-sm text-muted-foreground">
          {t("mlTraining.features.empty")}
        </div>
      ) : (
        <div className="border rounded-xl overflow-hidden">
          <table className="w-full text-sm" aria-label="Feature profiles">
            <thead>
              <tr className="border-b bg-muted/40">
                <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Profile ID</th>
                <th className="text-left px-3 py-2.5 text-muted-foreground font-medium">Zoo</th>
                <th className="text-left px-3 py-2.5 text-muted-foreground font-medium">Universe</th>
                <th className="text-right px-3 py-2.5 text-muted-foreground font-medium">Selected</th>
                <th className="text-left px-3 py-2.5 text-muted-foreground font-medium hidden md:table-cell">Methods</th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((p) => (
                <tr key={p.profile_id} className="border-b last:border-0 hover:bg-muted/20">
                  <td className="px-4 py-2.5 font-mono text-xs">{p.profile_id}</td>
                  <td className="px-3 py-2.5 text-xs">{p.zoo}</td>
                  <td className="px-3 py-2.5 text-xs">{p.universe}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{p.n_selected}</td>
                  <td className="px-3 py-2.5 hidden md:table-cell">
                    <div className="flex gap-1">
                      {p.methods.map((m) => (
                        <span key={m} className="px-1.5 py-0.5 rounded bg-muted text-xs">{m}</span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// Compare View
// ===========================================================================
