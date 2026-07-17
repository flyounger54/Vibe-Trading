import { Activity, Ban, OctagonX, CheckCircle2 } from "lucide-react";
import type { LiveAction } from "@/lib/api";
import { AgentAvatar } from "@/components/chat/AgentAvatar";
import { liveActionLabel } from "@/pages/agent/helpers";

function liveActionStyle(kind: string): { icon: typeof Activity; tone: string } {
  switch (kind) {
    case "order_rejected":
    case "breach":
      return { icon: Ban, tone: "border-amber-500/40 bg-amber-500/5 text-amber-600 dark:text-amber-400" };
    case "halt_tripped":
      return { icon: OctagonX, tone: "border-destructive/40 bg-destructive/5 text-destructive" };
    case "mandate_committed":
    case "halt_cleared":
      return { icon: CheckCircle2, tone: "border-emerald-500/40 bg-emerald-500/5 text-emerald-600 dark:text-emerald-400" };
    default:
      return { icon: Activity, tone: "border-sky-500/40 bg-sky-500/5 text-sky-600 dark:text-sky-400" };
  }
}

export function LiveActionChip({ action }: { action: LiveAction }) {
  const { icon: Icon, tone } = liveActionStyle(action.kind);
  return (
    <div className="flex gap-3">
      <AgentAvatar />
      <div className="flex-1 min-w-0">
        <div className={["inline-flex max-w-full flex-wrap items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs", tone].join(" ")}>
          <Icon className="h-3 w-3 shrink-0" />
          <span className="shrink-0 font-medium uppercase tracking-wide text-[10px]">RUNTIME</span>
          <span className="shrink-0 font-medium">{liveActionLabel(action)}</span>
          {action.intent_normalized && (
            <span className="truncate text-foreground/80">· {action.intent_normalized}</span>
          )}
          {action.outcome && (
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">· {action.outcome}</span>
          )}
          {action.remote_tool && (
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">· {action.remote_tool}</span>
          )}
          {action.error && <span className="truncate text-destructive">· {action.error}</span>}
        </div>
      </div>
    </div>
  );
}
