import { useState } from "react";
import { useParams } from "react-router-dom";
import { BookOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import { BrowseView } from "./strategy-zoo/BrowseView";
import { DetailView } from "./strategy-zoo/DetailView";
import { GuideDrawer } from "./strategy-zoo/shared";

export function StrategyZoo() {
  const params = useParams<{ strategyId?: string }>();
  const [guideOpen, setGuideOpen] = useState(false);

  return (
    <>
      {params.strategyId ? (
        <DetailView strategyId={params.strategyId} />
      ) : (
        <BrowseView />
      )}

      <button
        type="button"
        onClick={() => setGuideOpen(true)}
        className={cn(
          "fixed bottom-6 right-6 z-30 flex items-center gap-2 px-4 py-2.5 rounded-full",
          "bg-primary text-primary-foreground shadow-lg",
          "hover:opacity-90 transition-all hover:shadow-xl",
          "text-sm font-medium",
          guideOpen && "opacity-0 pointer-events-none",
        )}
        title="打开操作指南"
      >
        <BookOpen className="h-4 w-4" />
        操作指南
      </button>

      <GuideDrawer open={guideOpen} onClose={() => setGuideOpen(false)} />
    </>
  );
}
