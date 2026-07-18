import { useEffect, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";

// The 6 chokepoint dimensions, in display order, with max points and a short
// zh label. Keys match the backend chokepoint_score map.
const DIMENSIONS: Array<{ key: string; label: string; max: number }> = [
  { key: "supply_concentration", label: "供给集中度", max: 22 },
  { key: "irreplaceability", label: "不可替代性", max: 22 },
  { key: "supply_demand_gap", label: "供需缺口", max: 16 },
  { key: "certification_barrier", label: "认证壁垒", max: 16 },
  { key: "information_asymmetry", label: "信息不对称", max: 14 },
  { key: "catalyst_optionality", label: "催化剂", max: 10 },
];

interface Props {
  scores: Record<string, number>;
  height?: number;
}

/** 6-dimension chokepoint radar. Renders nothing when no scores are present. */
export function ChokepointRadar({ scores, height = 260 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  const hasData = DIMENSIONS.some((d) => typeof scores[d.key] === "number");

  useEffect(() => {
    if (!ref.current || !hasData) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);

    chart.setOption({
      backgroundColor: "transparent",
      tooltip: {},
      radar: {
        indicator: DIMENSIONS.map((d) => ({ name: d.label, max: d.max })),
        radius: "65%",
        axisName: { color: t.textColor, fontSize: 11 },
        splitLine: { lineStyle: { color: t.gridColor } },
        splitArea: { areaStyle: { color: ["transparent"] } },
        axisLine: { lineStyle: { color: t.gridColor } },
      },
      series: [
        {
          type: "radar",
          data: [
            {
              value: DIMENSIONS.map((d) => scores[d.key] ?? 0),
              name: "卡脖子评分",
              areaStyle: { color: t.upColor + "33" },
              lineStyle: { color: t.upColor, width: 2 },
              itemStyle: { color: t.upColor },
            },
          ],
        },
      ],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [scores, dark, hasData]);

  if (!hasData) {
    return (
      <div
        className="flex items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground"
        style={{ height }}
      >
        暂无评分数据
      </div>
    );
  }
  return <div ref={ref} style={{ height }} role="img" aria-label="六维卡点评分雷达图" />;
}
