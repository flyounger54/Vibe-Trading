import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Play, Loader2, AlertTriangle, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type MLTrainRequest, type MLTrainProgress, type MLTrainResult } from "@/lib/api";
import { AuthenticatedEventStream } from "@/lib/fetchSSE";
import { ContextTip, FormField, ErrorState } from "./shared";

export function TrainView() {
  const { t } = useTranslation();
  const [form, setForm] = useState<MLTrainRequest>({
    universe: "csi300",
    period: "2020-2024",
    zoo: "qlib158",
    model_type: "lightgbm",
    label_horizon: 5,
    label_type: "binary",
    cost_bps: 0,
    n_splits: 5,
  });
  const [training, setTraining] = useState(false);
  const [progress, setProgress] = useState<MLTrainProgress[]>([]);
  const [result, setResult] = useState<MLTrainResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const doneRef = useRef(false);

  const handleTrain = async () => {
    setTraining(true);
    setProgress([]);
    setResult(null);
    setError(null);
    doneRef.current = false;

    try {
      const { job_id } = await api.startMLTrain(form);
      const url = api.mlTrainStreamUrl(job_id);
      const source = new AuthenticatedEventStream(url);

      source.addEventListener("progress", (e) => {
        if (doneRef.current) return;
        const data = JSON.parse((e as MessageEvent).data) as MLTrainProgress;
        setProgress((prev) => [...prev, data]);
      });

      source.addEventListener("result", (e) => {
        doneRef.current = true;
        const data = JSON.parse((e as MessageEvent).data) as MLTrainResult;
        setResult(data);
        setTraining(false);
        source.close();
      });

      source.addEventListener("error", (e) => {
        if (doneRef.current) return;
        doneRef.current = true;
        try {
          const data = JSON.parse((e as MessageEvent).data);
          setError(data.error || "Training failed");
        } catch {
          setError("Training connection lost");
        }
        setTraining(false);
        source.close();
      });

      source.addEventListener("done", () => {
        doneRef.current = true;
        source.close();
      });

      source.onerror = () => {
        if (!doneRef.current) {
          doneRef.current = true;
          setError("SSE connection failed");
          setTraining(false);
        }
        source.close();
      };
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start training");
      setTraining(false);
    }
  };

  const update = (key: string, value: unknown) => setForm((f) => ({ ...f, [key]: value }));

  return (
    <div className="space-y-4">
      <ContextTip tipKey="train" />
      {/* Form */}
      <div className="border rounded-xl p-4 bg-card space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <FormField label={t("mlTraining.train.universe")}>
            <select value={form.universe} onChange={(e) => update("universe", e.target.value)} className="form-select">
              <option value="csi300">CSI 300</option>
              <option value="sp500">S&P 500</option>
            </select>
          </FormField>
          <FormField label={t("mlTraining.train.period")}>
            <input value={form.period} onChange={(e) => update("period", e.target.value)} className="form-input" placeholder="2020-2024" />
          </FormField>
          <FormField label={t("mlTraining.train.zoo")}>
            <select value={form.zoo} onChange={(e) => update("zoo", e.target.value)} className="form-select">
              <option value="qlib158">Qlib 158</option>
              <option value="alpha101">Alpha 101</option>
              <option value="gtja191">GTJA 191</option>
              <option value="academic">Academic</option>
            </select>
          </FormField>
          <FormField label={t("mlTraining.train.modelType")}>
            <select value={form.model_type} onChange={(e) => update("model_type", e.target.value)} className="form-select">
              <option value="lightgbm">LightGBM</option>
              <option value="xgboost">XGBoost</option>
              <option value="ridge">Ridge</option>
              <option value="deep_nn">Deep NN</option>
            </select>
          </FormField>
          <FormField label={t("mlTraining.train.horizon")}>
            <select value={form.label_horizon} onChange={(e) => update("label_horizon", Number(e.target.value))} className="form-select">
              {[1, 3, 5, 7, 10, 14, 20].map((d) => (
                <option key={d} value={d}>{d} {t("mlTraining.train.days")}</option>
              ))}
            </select>
          </FormField>
          <FormField label={t("mlTraining.train.labelType")}>
            <select value={form.label_type} onChange={(e) => update("label_type", e.target.value)} className="form-select">
              <option value="binary">Binary</option>
              <option value="return">Return</option>
              <option value="rank">Rank</option>
              <option value="top_bottom">Top/Bottom</option>
            </select>
          </FormField>
          <FormField label={t("mlTraining.train.benchmark")}>
            <input value={form.benchmark || ""} onChange={(e) => update("benchmark", e.target.value || undefined)} className="form-input" placeholder="000300.SH" />
          </FormField>
          <FormField label={t("mlTraining.train.costBps")}>
            <input type="number" value={form.cost_bps} onChange={(e) => update("cost_bps", Number(e.target.value))} className="form-input" min={0} step={5} />
          </FormField>
          <FormField label={t("mlTraining.train.cvFolds")}>
            <input type="number" value={form.n_splits} onChange={(e) => update("n_splits", Number(e.target.value))} className="form-input" min={2} max={10} />
          </FormField>
        </div>
        <div className="flex justify-end">
          <button
            onClick={handleTrain}
            disabled={training}
            className="inline-flex items-center gap-2 px-5 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50 transition"
          >
            {training ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {training ? t("mlTraining.train.training") : t("mlTraining.train.start")}
          </button>
        </div>
      </div>

      {/* Progress */}
      {progress.length > 0 && (
        <div className="border rounded-xl p-4 bg-card">
          <h3 className="text-sm font-medium mb-2">{t("mlTraining.train.progress")}</h3>
          <div className="space-y-1 max-h-40 overflow-y-auto">
            {progress.map((p, i) => (
              <div key={i} className="text-xs text-muted-foreground flex items-center gap-2">
                <ChevronRight className="h-3 w-3 shrink-0" />
                <span className="font-mono">{p.stage}</span>
                {p.message && <span>— {p.message}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Error */}
      {error && <ErrorState message={error} />}

      {/* Result */}
      {result && <TrainResultPanel result={result} />}
    </div>
  );
}

function TrainResultPanel({ result }: { result: MLTrainResult }) {
  const { t } = useTranslation();

  const stats = [
    { label: "Model ID", value: result.model_id, mono: true },
    { label: "Features", value: result.n_features },
    { label: "Samples", value: result.n_train_samples.toLocaleString() },
    { label: "Time", value: `${result.wall_seconds.toFixed(1)}s` },
  ];

  const cvKeys = Object.entries(result.cv_summary).filter(([, v]) => v != null);
  const topFeatures = Object.entries(result.top_features).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-4">
      {result.overfit_warning && (
        <div className="border border-warning/30 rounded-xl p-3 bg-warning/5 flex items-center gap-2 text-sm">
          <AlertTriangle className="h-4 w-4 text-warning shrink-0" />
          {t("mlTraining.train.overfitWarning")}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {stats.map((s) => (
          <div key={s.label} className="border rounded-xl p-3 bg-card">
            <p className="text-xs text-muted-foreground">{s.label}</p>
            <p className={cn("text-lg font-bold tabular-nums", s.mono && "font-mono text-sm")}>{s.value}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* CV Metrics */}
        <div className="border rounded-xl p-4 bg-card">
          <h3 className="text-sm font-medium mb-2">{t("mlTraining.train.cvMetrics")}</h3>
          <div className="space-y-1.5">
            {cvKeys.map(([k, v]) => (
              <div key={k} className="flex justify-between text-sm">
                <span className="text-muted-foreground">{k}</span>
                <span className="font-mono tabular-nums">{typeof v === "number" ? v.toFixed(4) : v}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Top Features */}
        <div className="border rounded-xl p-4 bg-card">
          <h3 className="text-sm font-medium mb-2">{t("mlTraining.train.topFeatures")}</h3>
          <div className="space-y-1">
            {topFeatures.slice(0, 10).map(([name, imp]) => (
              <div key={name} className="flex items-center gap-2 text-sm">
                <div className="flex-1 min-w-0">
                  <div className="flex justify-between mb-0.5">
                    <span className="font-mono text-xs truncate">{name}</span>
                    <span className="text-muted-foreground tabular-nums text-xs">{(imp * 100).toFixed(1)}%</span>
                  </div>
                  <div className="h-1 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary rounded-full"
                      style={{ width: `${Math.min(imp / (topFeatures[0]?.[1] || 1) * 100, 100)}%` }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ===========================================================================
// Features View
// ===========================================================================
