import type { ChainSegment } from "@/lib/api";
import { ChokepointRadar } from "./ChokepointRadar";
import { TierBadge } from "./ChainOverview";
import { EvidenceText } from "./EvidenceBadge";
import { EvidenceStateBadge } from "./EvidenceBadge";

interface Props {
  segment: ChainSegment;
}

/** Per-segment detail: positioning + competition + barrier cards, the
 * 6-dimension chokepoint radar, and the core-target table. */
export function SegmentDetail({ segment }: Props) {
  const supported = segment.evidence_state === "supported";
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">{segment.name}</h2>
        <EvidenceStateBadge state={segment.evidence_state} />
        {segment.positioning && (
          <span className="rounded-md border px-2 py-0.5 text-xs text-muted-foreground">
            {segment.positioning}
          </span>
        )}
        {segment.barrier_type && (
          <span className="rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
            {segment.barrier_type}
          </span>
        )}
        {supported && segment.chokepoint_total != null && (
          <span className="rounded-md bg-red-500/15 px-2 py-0.5 text-xs font-semibold text-red-600 dark:text-red-400">
            卡脖子 {segment.chokepoint_total}
          </span>
        )}
      </div>

      {!supported && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-4 text-sm text-muted-foreground">
          此环节的证据{segment.evidence_state === "stale" ? "已过期" : segment.evidence_state === "conflicting" ? "存在冲突" : "不足"}，下方不展示为确定性评分或竞争结论。
        </div>
      )}

      {supported ? (
        <>
          <div className="grid gap-6 lg:grid-cols-2">
            <div className="space-y-4">
              <InfoCard label="价值量占比" value={segment.value_weight} />
              <InfoCard label="国产化进展" value={segment.localization_rate} />
              <InfoCard label="壁垒说明" value={segment.barrier_description} />
            </div>
            <div className="rounded-md border bg-card p-5">
              <h3 className="mb-2 text-sm font-semibold text-muted-foreground">6维卡脖子评分</h3>
              <ChokepointRadar scores={segment.chokepoint_score} />
            </div>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <CompetitionCard title="国际竞争格局" value={segment.international_competition} />
            <CompetitionCard title="国内竞争格局" value={segment.domestic_competition} />
          </div>

          <TickerTable segment={segment} />
        </>
      ) : (
        <div className="rounded-md border border-dashed p-5 text-xs text-muted-foreground">
          等待可核验且未冲突的来源后再显示该环节结论。
        </div>
      )}
    </div>
  );
}

function InfoCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border bg-card p-4">
      <div className="mb-1 text-xs font-medium text-muted-foreground">{label}</div>
      <div className="text-sm">{value ? <EvidenceText text={value} /> : <span className="text-muted-foreground">待补</span>}</div>
    </div>
  );
}

function CompetitionCard({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-md border bg-card p-5">
      <h3 className="mb-2 text-sm font-semibold text-muted-foreground">{title}</h3>
      <p className="text-sm leading-relaxed">
        {value ? <EvidenceText text={value} /> : <span className="text-muted-foreground">待补</span>}
      </p>
    </div>
  );
}

function TickerTable({ segment }: { segment: ChainSegment }) {
  if (segment.tickers.length === 0) {
    return (
      <div className="rounded-md border bg-card p-5 text-xs text-muted-foreground">
        暂无标的，分析后自动填充。
      </div>
    );
  }
  return (
    <div className="rounded-md border bg-card p-5">
      <h3 className="mb-3 text-sm font-semibold text-muted-foreground">核心标的</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th className="pb-2 pr-4 font-medium">代码</th>
              <th className="pb-2 pr-4 font-medium">名称</th>
              <th className="pb-2 pr-4 font-medium">评分</th>
              <th className="pb-2 pr-4 font-medium">分级</th>
              <th className="pb-2 pr-4 font-medium">分类</th>
              <th className="pb-2 font-medium">红队结论</th>
            </tr>
          </thead>
          <tbody>
            {segment.tickers.map((tk) => (
              <tr key={tk.code} className="border-b last:border-0 align-top">
                <td className="py-2 pr-4 font-mono text-xs">{tk.code}</td>
                <td className="py-2 pr-4">{tk.name}</td>
                <td className="py-2 pr-4 font-semibold">{tk.score ?? "—"}</td>
                <td className="py-2 pr-4">
                  <TierBadge tier={tk.tier} />
                </td>
                <td className="py-2 pr-4 text-xs text-muted-foreground">{tk.classification || "—"}</td>
                <td className="py-2 text-xs text-muted-foreground">{tk.red_team_note || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
