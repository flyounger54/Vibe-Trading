import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { Brain, Play, Filter, GitCompare, HeartPulse, BookOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import { ModelsView } from "./ml-training/ModelsView";
import { TrainView } from "./ml-training/TrainView";
import { FeaturesView } from "./ml-training/FeaturesView";
import { CompareView } from "./ml-training/CompareView";
import { HealthView } from "./ml-training/HealthView";
import { GuideView } from "./ml-training/GuideView";

type Tab = "models" | "train" | "features" | "compare" | "health" | "guide";

const TABS: { key: Tab; icon: typeof Brain; labelKey: string }[] = [
  { key: "models", icon: Brain, labelKey: "mlTraining.tabs.models" },
  { key: "train", icon: Play, labelKey: "mlTraining.tabs.train" },
  { key: "features", icon: Filter, labelKey: "mlTraining.tabs.features" },
  { key: "compare", icon: GitCompare, labelKey: "mlTraining.tabs.compare" },
  { key: "health", icon: HeartPulse, labelKey: "mlTraining.tabs.health" },
  { key: "guide", icon: BookOpen, labelKey: "mlTraining.tabs.guide" },
];

export function MLTraining() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();

  const pathTab = location.pathname.replace("/ml-training", "").replace("/", "") as Tab;
  const activeTab: Tab = TABS.some((tb) => tb.key === pathTab) ? pathTab : "models";

  const setTab = (tab: Tab) => {
    navigate(tab === "models" ? "/ml-training" : `/ml-training/${tab}`);
  };

  return (
    <div className="p-4 md:p-8 max-w-6xl mx-auto space-y-6">
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-xs text-muted-foreground uppercase tracking-wide">
          <Brain className="h-3.5 w-3.5" aria-hidden="true" />
          {t("mlTraining.label")}
        </div>
        <h1 className="text-2xl md:text-3xl font-bold tracking-tight">
          {t("mlTraining.title")}
        </h1>
        <p className="text-sm text-muted-foreground max-w-2xl">
          {t("mlTraining.description")}
        </p>
      </div>

      <div className="flex gap-1 border rounded-xl p-1 bg-muted/30 overflow-x-auto">
        {TABS.map(({ key, icon: Icon, labelKey }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition whitespace-nowrap",
              activeTab === key
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground hover:bg-card/50"
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {t(labelKey)}
          </button>
        ))}
      </div>

      {activeTab === "models" && <ModelsView />}
      {activeTab === "train" && <TrainView />}
      {activeTab === "features" && <FeaturesView />}
      {activeTab === "compare" && <CompareView />}
      {activeTab === "health" && <HealthView />}
      {activeTab === "guide" && <GuideView />}
    </div>
  );
}
