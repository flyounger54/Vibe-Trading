import { useEffect, useRef, useState } from "react";
import { GitCompare, Loader2 } from "lucide-react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import { api, type ChainSummary } from "@/lib/api";

interface CompareChain {
  chain_id: string;
  name: string;
  status: string;
  lifecycle_stage: string;
  prosperity_score: number | null;
  sector_score: number | null;
  segment_scores: Record<string, number>;
  segment_names: string[];
}

interface SharedTicker {
  code: string;
  name: string;
  chains: string[];
}

interface CompareResult {
  chains: CompareChain[];
  shared_tickers: SharedTicker[];
}

interface Props {
  chains: ChainSummary[];
}

const COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6"];

export function ChainCompare({ chains }: Props) {
  const [selected, setSelected] = useState<string[]>([]);
  const [result, setResult] = useState<CompareResult | null>(null);
  const [loading, setLoading] = useState(false);

  const toggle = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : prev.length < 4 ? [...prev, id] : prev,
    );
  };

  const runCompare = async () => {
    if (selected.length < 2) return;
    setLoading(true);
    try {
      const r = await api.compareChains(selected.join(","));
      setResult(r);
    } catch {
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="mb-2 text-sm font-semibold text-muted-foreground">选择 2-4 条链对比</h3>
        <div className="flex flex-wrap gap-2">
          {chains.map((c) => (
            <button
              key={c.chain_id}
              onClick={() => toggle(c.chain_id)}
              className={`rounded-md border px-3 py-1.5 text-sm transition ${
                selected.includes(c.chain_id) ? "border-primary bg-primary/10 font-medium" : "hover:bg-muted"
              }`}
            >
              {c.name}
            </button>
          ))}
        </div>
        <button
          onClick={runCompare}
          disabled={selected.length < 2 || loading}
          className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitCompare className="h-4 w-4" />}
          对比
        </button>
      </div>

      {result && (
        <div className="space-y-6">
          <div className="grid gap-6 lg:grid-cols-2">
            <ProsperityCompare chains={result.chains} />
            <SegmentRadarOverlay chains={result.chains} />
          </div>
          <SharedTickersTable tickers={result.shared_tickers} />
        </div>
      )}
    </div>
  );
}

function ProsperityCompare({ chains }: { chains: CompareChain[] }) {
  return (
    <div className="rounded-md border bg-card p-5">
      <h3 className="mb-3 text-sm font-semibold text-muted-foreground">链级景气度对比</h3>
      <div className="space-y-2">
        {chains.map((c, i) => (
          <div key={c.chain_id} className="flex items-center gap-3">
            <div className="h-3 w-3 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />
            <span className="w-24 text-sm font-medium">{c.name}</span>
            <div className="flex-1">
              <div className="h-3 rounded-full bg-muted">
                <div
                  className="h-3 rounded-full"
                  style={{
                    width: `${c.prosperity_score ?? 0}%`,
                    background: COLORS[i % COLORS.length],
                  }}
                />
              </div>
            </div>
            <span className="w-8 text-right text-sm font-semibold">{c.prosperity_score ?? "—"}</span>
            {c.lifecycle_stage && (
              <span className="rounded px-1.5 py-0.5 text-xs text-muted-foreground">{c.lifecycle_stage}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function SegmentRadarOverlay({ chains }: { chains: CompareChain[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  useEffect(() => {
    if (!ref.current || chains.length === 0) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);

    const allSegments = [...new Set(chains.flatMap((c) => Object.keys(c.segment_scores)))];
    if (allSegments.length === 0) {
      chart.dispose();
      return;
    }

    chart.setOption({
      backgroundColor: "transparent",
      tooltip: {},
      legend: {
        data: chains.map((c) => c.name),
        textStyle: { color: t.textColor },
        bottom: 0,
      },
      radar: {
        indicator: allSegments.map((s) => ({ name: s, max: 100 })),
        radius: "60%",
        axisName: { color: t.textColor, fontSize: 10 },
        splitLine: { lineStyle: { color: t.gridColor } },
        splitArea: { areaStyle: { color: ["transparent"] } },
        axisLine: { lineStyle: { color: t.gridColor } },
      },
      series: [
        {
          type: "radar",
          data: chains.map((c, i) => ({
            value: allSegments.map((s) => c.segment_scores[s] ?? 0),
            name: c.name,
            lineStyle: { color: COLORS[i % COLORS.length], width: 2 },
            areaStyle: { color: COLORS[i % COLORS.length] + "22" },
            itemStyle: { color: COLORS[i % COLORS.length] },
          })),
        },
      ],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [chains, dark]);

  const hasScores = chains.some((c) => Object.keys(c.segment_scores).length > 0);
  if (!hasScores) {
    return (
      <div className="flex h-64 items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground">
        所选链暂无评分数据
      </div>
    );
  }
  return (
    <div className="rounded-md border bg-card p-5">
      <h3 className="mb-2 text-sm font-semibold text-muted-foreground">环节评分雷达叠加</h3>
      <div ref={ref} style={{ height: 280 }} role="img" aria-label="产业链卡点雷达对比图" />
    </div>
  );
}

function SharedTickersTable({ tickers }: { tickers: SharedTicker[] }) {
  if (tickers.length === 0) {
    return (
      <div className="rounded-md border bg-card p-5 text-xs text-muted-foreground">
        未发现跨链共现标的。同时出现在多条链的公司 = 平台型公司，投资价值可能更大。
      </div>
    );
  }
  return (
    <div className="rounded-md border bg-card p-5">
      <h3 className="mb-3 text-sm font-semibold text-muted-foreground">
        跨链共现标的（平台型公司）
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th className="pb-2 pr-4 font-medium">代码</th>
              <th className="pb-2 pr-4 font-medium">名称</th>
              <th className="pb-2 font-medium">出现在</th>
            </tr>
          </thead>
          <tbody>
            {tickers.map((tk) => (
              <tr key={tk.code} className="border-b last:border-0">
                <td className="py-2 pr-4 font-mono text-xs">{tk.code}</td>
                <td className="py-2 pr-4">{tk.name}</td>
                <td className="py-2">
                  {tk.chains.map((c) => (
                    <span key={c} className="mr-1.5 rounded bg-muted px-1.5 py-0.5 text-xs">
                      {c}
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
