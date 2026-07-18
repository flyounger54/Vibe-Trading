import { useEffect, useRef, useState } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import { api, type ChainSnapshot } from "@/lib/api";
import { ErrorRetryBanner } from "@/components/common/ErrorRetryBanner";

interface Props {
  chainId: string;
}

const STAGE_COLOR: Record<string, string> = {
  Discovery: "#22c55e",
  Validation: "#3b82f6",
  Mainstream: "#f59e0b",
  Exhaustion: "#ef4444",
};

/** Prosperity time-series: plots prosperity_score and lifecycle_stage over
 * multiple analysis snapshots from the chain history API. */
export function ProsperityHistory({ chainId }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const [snapshots, setSnapshots] = useState<ChainSnapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    api
      .getChainHistory(chainId)
      .then((r) => setSnapshots(r.snapshots))
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "景气度历史加载失败"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, [chainId]);

  useEffect(() => {
    if (!ref.current || snapshots.length < 1) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);

    const dates = snapshots.map((s) => s.recorded_at.slice(0, 10));
    const prosperity = snapshots.map((s) => s.prosperity_score ?? 0);
    const sector = snapshots.map((s) => s.sector_score ?? 0);

    chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: {
        data: ["景气度", "板块评分"],
        textStyle: { color: t.textColor },
        bottom: 0,
      },
      grid: { left: 45, right: 20, top: 20, bottom: 40 },
      xAxis: {
        type: "category",
        data: dates,
        axisLabel: { color: t.textColor, fontSize: 10 },
        axisLine: { lineStyle: { color: t.axisColor } },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        axisLabel: { color: t.textColor },
        splitLine: { lineStyle: { color: t.gridColor } },
      },
      series: [
        {
          name: "景气度",
          type: "line",
          data: prosperity,
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { color: "#3b82f6", width: 2 },
          itemStyle: {
            color: (params: { dataIndex: number }) => {
              const stage = snapshots[params.dataIndex]?.lifecycle_stage ?? "";
              return STAGE_COLOR[stage] ?? "#3b82f6";
            },
          },
          areaStyle: {
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "#3b82f630" },
                { offset: 1, color: "#3b82f605" },
              ],
            },
          },
        },
        {
          name: "板块评分",
          type: "line",
          data: sector,
          smooth: true,
          symbol: "diamond",
          symbolSize: 5,
          lineStyle: { color: t.upColor, width: 1.5, type: "dashed" },
          itemStyle: { color: t.upColor },
        },
      ],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [snapshots, dark]);

  if (loading) {
    return <div className="flex h-32 items-center justify-center text-xs text-muted-foreground" role="status">正在加载景气度历史…</div>;
  }

  if (error) {
    return <ErrorRetryBanner message={error} onRetry={load} />;
  }

  if (snapshots.length < 1) {
    return (
      <div className="flex h-32 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground">
        多次分析后展示景气度趋势（每次分析自动记录快照）
      </div>
    );
  }

  return (
    <div>
      <div ref={ref} style={{ height: 220 }} role="img" aria-label="产业链景气度历史趋势图" />
      <div className="mt-1 flex flex-wrap gap-2 px-1">
        {Object.entries(STAGE_COLOR).map(([stage, color]) => (
          <span key={stage} className="flex items-center gap-1 text-[10px] text-muted-foreground">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />
            {stage}
          </span>
        ))}
      </div>
    </div>
  );
}
