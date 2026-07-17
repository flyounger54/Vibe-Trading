import { ShieldAlert } from "lucide-react";
import type { Chain } from "@/lib/api";
import { TierBadge } from "./ChainOverview";

interface Props {
  chain: Chain;
}

/** Red-team tab: surfaces the adversarial notes the red_team_challenger agent
 * produced for each ticker, grouped by segment. This is the "辩论/红队记录"
 * view — the data already lives on each ticker's red_team_note. */
export function RedTeamLog({ chain }: Props) {
  const rows = chain.segments
    .map((seg) => ({
      segment: seg.name,
      tickers: seg.tickers.filter((t) => t.red_team_note),
    }))
    .filter((g) => g.tickers.length > 0);

  if (rows.length === 0) {
    return (
      <div className="flex h-40 flex-col items-center justify-center gap-2 rounded-md border border-dashed text-sm text-muted-foreground">
        <ShieldAlert className="h-6 w-6" />
        暂无红队记录。运行分析后，对手方视角的攻击结论将展示于此。
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <ShieldAlert className="h-4 w-4" />
        红队对每个高分标的执行 9 维攻击向量（估值/供给弹性/技术路径/拥挤度/稀释/流动性/客户集中/地缘/替代），先写空头论点再给裁决。
      </div>
      {rows.map((group) => (
        <div key={group.segment} className="rounded-md border bg-card p-5">
          <h3 className="mb-3 text-sm font-semibold">{group.segment}</h3>
          <div className="space-y-3">
            {group.tickers.map((tk) => (
              <div key={tk.code} className="flex gap-3 border-l-2 border-amber-500/50 pl-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{tk.name}</span>
                    <span className="font-mono text-xs text-muted-foreground">{tk.code}</span>
                    <TierBadge tier={tk.tier} />
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{tk.red_team_note}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
