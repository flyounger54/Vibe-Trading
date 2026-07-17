import { useEffect, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { Chain } from "@/lib/api";
import { ProsperityHistory } from "./ProsperityHistory";

interface Props {
  chain: Chain;
}

const STAGE_COLOR: Record<string, string> = {
  Discovery: "#22c55e",
  Validation: "#3b82f6",
  Mainstream: "#f59e0b",
  Exhaustion: "#ef4444",
};

/** Overview tab: structure summary + prosperity gauge + chokepoint comparison
 * bar (segment-level) + core targets pool. */
export function ChainOverview({ chain }: Props) {
  const { overview, segments } = chain;
  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-md border bg-card p-5">
          <h3 className="mb-2 text-sm font-semibold text-muted-foreground">产业链格局</h3>
          <p className="text-sm leading-relaxed">
            {overview.structure_summary || "尚未分析。点击「一键分析」生成产业链结构与卡口判断。"}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {overview.lifecycle_stage && (
              <span
                className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium text-white"
                style={{ background: STAGE_COLOR[overview.lifecycle_stage] ?? "#64748b" }}
              >
                生命周期 · {overview.lifecycle_stage}
              </span>
            )}
            {overview.sector_score != null && (
              <span className="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs font-medium text-muted-foreground">
                板块评分 {overview.sector_score}
              </span>
            )}
          </div>
        </div>

        <div className="rounded-md border bg-card p-5">
          <h3 className="mb-2 text-sm font-semibold text-muted-foreground">链级景气度</h3>
          <ProsperityGauge value={overview.prosperity_score} />
          <h3 className="mb-2 mt-4 text-sm font-semibold text-muted-foreground">景气度趋势</h3>
          <ProsperityHistory chainId={chain.chain_id} />
        </div>
      </div>

      <div className="rounded-md border bg-card p-5">
        <h3 className="mb-3 text-sm font-semibold text-muted-foreground">
          各环节卡脖子评分对比
        </h3>
        <ChokepointBar segments={segments} />
      </div>

      <CoreTargetsTable chain={chain} />
    </div>
  );
}

function ProsperityGauge({ value }: { value: number | null }) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  useEffect(() => {
    if (!ref.current) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);
    chart.setOption({
      backgroundColor: "transparent",
      series: [
        {
          type: "gauge",
          min: 0,
          max: 100,
          progress: { show: true, width: 14 },
          axisLine: { lineStyle: { width: 14, color: [[1, t.gridColor]] } },
          axisLabel: { color: t.textColor, fontSize: 10, distance: 14 },
          axisTick: { show: false },
          splitLine: { length: 10, lineStyle: { color: t.gridColor } },
          pointer: { width: 5 },
          detail: {
            valueAnimation: true,
            formatter: value == null ? "—" : "{value}",
            color: t.textColor,
            fontSize: 24,
            offsetCenter: [0, "60%"],
          },
          data: [{ value: value ?? 0 }],
        },
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [value, dark]);
  return <div ref={ref} style={{ height: 200 }} />;
}

function ChokepointBar({ segments }: { segments: Chain["segments"] }) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const scored = segments.filter((s) => s.chokepoint_total != null);

  useEffect(() => {
    if (!ref.current || scored.length === 0) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);
    const sorted = [...scored].sort(
      (a, b) => (a.chokepoint_total ?? 0) - (b.chokepoint_total ?? 0),
    );
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 90, right: 30, top: 10, bottom: 20 },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: {
        type: "value",
        max: 100,
        axisLabel: { color: t.textColor },
        splitLine: { lineStyle: { color: t.gridColor } },
      },
      yAxis: {
        type: "category",
        data: sorted.map((s) => s.name),
        axisLabel: { color: t.textColor },
        axisLine: { lineStyle: { color: t.axisColor } },
      },
      series: [
        {
          type: "bar",
          data: sorted.map((s) => ({
            value: s.chokepoint_total,
            itemStyle: {
              color:
                (s.chokepoint_total ?? 0) >= 80
                  ? "#ef4444"
                  : (s.chokepoint_total ?? 0) >= 60
                    ? "#f59e0b"
                    : t.upColor,
            },
          })),
          barWidth: "55%",
          label: { show: true, position: "right", color: t.textColor },
        },
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [scored, dark]);

  if (scored.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground">
        暂无评分数据，运行分析后展示
      </div>
    );
  }
  return <div ref={ref} style={{ height: Math.max(160, scored.length * 38) }} />;
}

function CoreTargetsTable({ chain }: { chain: Chain }) {
  const targets = chain.overview.core_targets;
  if (targets.length === 0) {
    return (
      <div className="rounded-md border bg-card p-5">
        <h3 className="mb-2 text-sm font-semibold text-muted-foreground">核心标的池</h3>
        <p className="text-xs text-muted-foreground">分析完成后，各环节 Core/Build 标的将汇总于此。</p>
      </div>
    );
  }
  return (
    <div className="rounded-md border bg-card p-5">
      <h3 className="mb-3 text-sm font-semibold text-muted-foreground">核心标的池</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th className="pb-2 pr-4 font-medium">代码</th>
              <th className="pb-2 pr-4 font-medium">名称</th>
              <th className="pb-2 pr-4 font-medium">评分</th>
              <th className="pb-2 pr-4 font-medium">分级</th>
              <th className="pb-2 pr-4 font-medium">分类</th>
              <th className="pb-2 font-medium">置信度</th>
            </tr>
          </thead>
          <tbody>
            {targets.map((tk) => (
              <tr key={tk.code} className="border-b last:border-0">
                <td className="py-2 pr-4 font-mono text-xs">{tk.code}</td>
                <td className="py-2 pr-4">{tk.name}</td>
                <td className="py-2 pr-4 font-semibold">{tk.score ?? "—"}</td>
                <td className="py-2 pr-4">
                  <TierBadge tier={tk.tier} />
                </td>
                <td className="py-2 pr-4 text-xs text-muted-foreground">{tk.classification || "—"}</td>
                <td className="py-2 text-xs text-muted-foreground">{tk.confidence || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function TierBadge({ tier }: { tier: string }) {
  if (!tier) return <span className="text-xs text-muted-foreground">—</span>;
  const color: Record<string, string> = {
    Core: "bg-red-500/15 text-red-600 dark:text-red-400",
    Build: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
    Watch: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
    Skip: "bg-muted text-muted-foreground",
  };
  return (
    <span className={`inline-flex rounded px-1.5 py-0.5 text-xs font-medium ${color[tier] ?? "bg-muted text-muted-foreground"}`}>
      {tier}
    </span>
  );
}
