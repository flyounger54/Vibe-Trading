import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Shield, Workflow, BarChart3, Zap, ChevronRight, Brain, Filter, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

export function GuideView() {
  const { t } = useTranslation();
  return (
    <div className="space-y-6 max-w-4xl">
      {/* Overview */}
      <GuideSection
        icon={Workflow}
        title={t("mlTraining.guide.overview.title")}
        defaultOpen
      >
        <p>{t("mlTraining.guide.overview.p1")}</p>
        <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
          {(["step1", "step2", "step3", "step4"] as const).map((key, i) => (
            <div key={key} className="flex gap-3 p-3 rounded-lg bg-muted/30 border">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold">{i + 1}</span>
              <div>
                <p className="text-sm font-medium">{t(`mlTraining.guide.overview.${key}.title`)}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{t(`mlTraining.guide.overview.${key}.desc`)}</p>
              </div>
            </div>
          ))}
        </div>
      </GuideSection>

      {/* Quick Start */}
      <GuideSection icon={Zap} title={t("mlTraining.guide.quickstart.title")}>
        <ol className="space-y-3 list-none">
          {([1,2,3,4,5] as const).map((n) => (
            <li key={n} className="flex gap-3">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-mono">{n}</span>
              <div>
                <p className="text-sm">{t(`mlTraining.guide.quickstart.s${n}`)}</p>
                {t(`mlTraining.guide.quickstart.s${n}code`, "") && (
                  <code className="block mt-1 text-xs bg-muted/50 rounded px-2 py-1 font-mono text-muted-foreground">
                    {t(`mlTraining.guide.quickstart.s${n}code`)}
                  </code>
                )}
              </div>
            </li>
          ))}
        </ol>
      </GuideSection>

      {/* Anti-Leakage */}
      <GuideSection icon={Shield} title={t("mlTraining.guide.leakage.title")}>
        <p>{t("mlTraining.guide.leakage.intro")}</p>
        <div className="mt-3 space-y-2">
          {(["purge", "embargo", "selectionLeak", "preprocessLeak", "survivorship", "cacheLeak"] as const).map((key) => (
            <div key={key} className="flex gap-2 text-sm">
              <Shield className="h-4 w-4 text-primary shrink-0 mt-0.5" />
              <div>
                <span className="font-medium">{t(`mlTraining.guide.leakage.${key}.name`)}</span>
                <span className="text-muted-foreground"> — {t(`mlTraining.guide.leakage.${key}.desc`)}</span>
              </div>
            </div>
          ))}
        </div>
      </GuideSection>

      {/* Feature Selection */}
      <GuideSection icon={Filter} title={t("mlTraining.guide.features.title")}>
        <p>{t("mlTraining.guide.features.intro")}</p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/30">
                <th className="text-left px-3 py-2 font-medium">{t("mlTraining.guide.features.method")}</th>
                <th className="text-left px-3 py-2 font-medium">{t("mlTraining.guide.features.description")}</th>
                <th className="text-left px-3 py-2 font-medium">{t("mlTraining.guide.features.speed")}</th>
              </tr>
            </thead>
            <tbody>
              {(["ic_filter", "corr_dedup", "mutual_info", "shap", "boruta", "importance"] as const).map((m) => (
                <tr key={m} className="border-b last:border-0">
                  <td className="px-3 py-2 font-mono text-xs">{m}</td>
                  <td className="px-3 py-2 text-muted-foreground">{t(`mlTraining.guide.features.methods.${m}`)}</td>
                  <td className="px-3 py-2">{t(`mlTraining.guide.features.speeds.${m}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </GuideSection>

      {/* Models */}
      <GuideSection icon={Brain} title={t("mlTraining.guide.models.title")}>
        <p>{t("mlTraining.guide.models.intro")}</p>
        <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
          {(["lightgbm", "xgboost", "ridge", "deep_nn"] as const).map((m) => (
            <div key={m} className="p-3 rounded-lg border bg-card">
              <p className="text-sm font-medium">{t(`mlTraining.guide.models.${m}.name`)}</p>
              <p className="text-xs text-muted-foreground mt-1">{t(`mlTraining.guide.models.${m}.desc`)}</p>
              <p className="text-xs text-muted-foreground mt-1">
                NaN: {t(`mlTraining.guide.models.${m}.nan`)} | {t(`mlTraining.guide.models.${m}.dep`)}
              </p>
            </div>
          ))}
        </div>
      </GuideSection>

      {/* Labels */}
      <GuideSection icon={BarChart3} title={t("mlTraining.guide.labels.title")}>
        <p>{t("mlTraining.guide.labels.intro")}</p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/30">
                <th className="text-left px-3 py-2 font-medium">{t("mlTraining.guide.labels.type")}</th>
                <th className="text-left px-3 py-2 font-medium">{t("mlTraining.guide.labels.output")}</th>
                <th className="text-left px-3 py-2 font-medium">{t("mlTraining.guide.labels.usage")}</th>
              </tr>
            </thead>
            <tbody>
              {(["return", "rank", "binary", "top_bottom"] as const).map((l) => (
                <tr key={l} className="border-b last:border-0">
                  <td className="px-3 py-2 font-mono text-xs">{l}</td>
                  <td className="px-3 py-2 text-muted-foreground">{t(`mlTraining.guide.labels.types.${l}.output`)}</td>
                  <td className="px-3 py-2 text-muted-foreground">{t(`mlTraining.guide.labels.types.${l}.usage`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-3 p-3 rounded-lg bg-muted/30 border text-sm space-y-1">
          <p className="font-medium">{t("mlTraining.guide.labels.advanced")}</p>
          <p className="text-muted-foreground">{t("mlTraining.guide.labels.benchmark")}</p>
          <p className="text-muted-foreground">{t("mlTraining.guide.labels.cost")}</p>
        </div>
      </GuideSection>

      {/* Ensemble & Health */}
      <GuideSection icon={RefreshCw} title={t("mlTraining.guide.lifecycle.title")}>
        <p>{t("mlTraining.guide.lifecycle.intro")}</p>
        <div className="mt-3 space-y-2">
          {(["ensemble", "sliding", "health", "promote"] as const).map((key) => (
            <div key={key} className="flex gap-2 text-sm">
              <ChevronRight className="h-4 w-4 text-primary shrink-0 mt-0.5" />
              <div>
                <span className="font-medium">{t(`mlTraining.guide.lifecycle.${key}.name`)}</span>
                <span className="text-muted-foreground"> — {t(`mlTraining.guide.lifecycle.${key}.desc`)}</span>
              </div>
            </div>
          ))}
        </div>
      </GuideSection>
    </div>
  );
}

function GuideSection({
  icon: Icon,
  title,
  defaultOpen = false,
  children,
}: {
  icon: typeof Brain;
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border rounded-xl bg-card overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-muted/20 transition"
      >
        <Icon className="h-4 w-4 text-primary shrink-0" />
        <span className="text-sm font-medium flex-1">{title}</span>
        <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="px-4 pb-4 text-sm text-foreground/90 leading-relaxed">
          {children}
        </div>
      )}
    </div>
  );
}
