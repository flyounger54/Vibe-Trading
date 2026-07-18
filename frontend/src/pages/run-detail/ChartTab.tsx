import i18n from "@/i18n";
import { useRef, useState, type ReactNode } from "react";
import { Loader2, Scale, Target, Shield, TrendingUp, Zap, BarChart3, X } from "lucide-react";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import type { RunData } from "@/lib/api";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { EquityChart } from "@/components/charts/EquityChart";
import { type ChartCache } from "./helpers";

type ChartLoadProgress = { done: number; total: number };

export function ChartTab({
  run,
  chartPickerSymbol,
  selectedSymbols,
  chartCache,
  loadingSymbols,
  bulkLoading,
  bulkProgress,
  onPickSymbol,
  onAddSymbol,
  onCurrentOnly,
  onRemoveSymbol,
  onLoadAll,
  onCancelLoadAll,
}: {
  run: RunData;
  chartPickerSymbol: string;
  selectedSymbols: string[];
  chartCache: ChartCache;
  loadingSymbols: Record<string, boolean>;
  bulkLoading: boolean;
  bulkProgress: ChartLoadProgress;
  onPickSymbol: (symbol: string) => void;
  onAddSymbol: (symbol: string) => void | Promise<void>;
  onCurrentOnly: (symbol: string) => void | Promise<void>;
  onRemoveSymbol: (symbol: string) => void;
  onLoadAll: () => void | Promise<void>;
  onCancelLoadAll: () => void;
}) {
  const chartSymbols = run.chart_symbols || Object.keys(run.price_series || {});
  const entries = selectedSymbols
    .map((symbol) => [symbol, chartCache[symbol]?.price_series?.[symbol] || []] as const)
    .filter(([, bars]) => bars.length > 0);
  const hasEquity = run.equity_curve && run.equity_curve.length > 0;
  const progressPercent = bulkProgress.total > 0 ? Math.round((bulkProgress.done / bulkProgress.total) * 100) : 0;

  if (chartSymbols.length === 0 && entries.length === 0 && !hasEquity) {
    return (
      <div className="p-8 text-center text-muted-foreground space-y-2">
        <p className="text-sm">{i18n.t("runDetail.noChartData")}</p>
        <p className="text-xs">{i18n.t("runDetail.noChartDataDesc")}</p>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      {chartSymbols.length > 0 && (
        <div className="rounded-md border bg-card p-3">
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-xs font-medium text-muted-foreground" htmlFor="chart-symbol-select">
              {i18n.t("runDetail.symbol")}
            </label>
            <select
              id="chart-symbol-select"
              value={chartPickerSymbol}
              onChange={(event) => onPickSymbol(event.target.value)}
              className="h-8 rounded-md border bg-background px-2 text-sm"
            >
              {chartSymbols.map((symbol) => (
                <option key={symbol} value={symbol}>{symbol}</option>
              ))}
            </select>
            <button
              onClick={() => onCurrentOnly(chartPickerSymbol)}
              className="rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted"
              disabled={!chartPickerSymbol || !!loadingSymbols[chartPickerSymbol]}
            >
              {loadingSymbols[chartPickerSymbol] ? <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" /> : null}
              {i18n.t("runDetail.showOnly")}
            </button>
            <button
              onClick={() => onAddSymbol(chartPickerSymbol)}
              className="rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted"
              disabled={!chartPickerSymbol || !!loadingSymbols[chartPickerSymbol]}
            >
              {i18n.t("runDetail.addSymbol")}
            </button>
            <button
              onClick={() => void onLoadAll()}
              className="rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted"
              disabled={bulkLoading}
            >
              {bulkLoading ? <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" /> : null}
              {i18n.t("runDetail.loadAll")}
            </button>
            {bulkLoading && (
              <button
                onClick={onCancelLoadAll}
                className="rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted"
              >
                {i18n.t("runDetail.cancelLoad")}
              </button>
            )}
          </div>
          {selectedSymbols.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {selectedSymbols.map((symbol) => (
                <button
                  key={symbol}
                  onClick={() => onRemoveSymbol(symbol)}
                  className="rounded-md bg-muted px-2 py-1 text-xs hover:bg-muted/80"
                >
                  {symbol} x
                </button>
              ))}
            </div>
          )}
          {bulkLoading && (
            <div className="mt-3 space-y-1">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>{i18n.t("runDetail.loadingCharts")}</span>
                <span>{bulkProgress.done}/{bulkProgress.total}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-muted">
                <div className="h-full bg-primary transition-all" style={{ width: `${progressPercent}%` }} />
              </div>
            </div>
          )}
        </div>
      )}
      {entries.length === 0 && (
        <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
          {Object.keys(loadingSymbols).length > 0 ? i18n.t("runDetail.loadingSelectedChart") : i18n.t("runDetail.pickSymbolToLoad")}
        </div>
      )}
      {entries.map(([sym, bars]) => (
        <div key={sym}>
          <h3 className="text-sm font-medium mb-1">{sym}</h3>
          <CandlestickChart data={bars} markers={chartCache[sym]?.trade_markers?.filter(m => m.code === sym)} indicators={chartCache[sym]?.indicator_series?.[sym]} height={500} />
        </div>
      ))}
      {hasEquity && (
        <div>
          <h3 className="text-sm font-medium mb-1">{i18n.t("runDetail.equityDrawdown")}</h3>
          <EquityChart data={run.equity_curve!} height={280} />
        </div>
      )}
    </div>
  );
}

/* ========== Position Sizing Guide ========== */

interface GuideSection {
  id: string;
  icon: typeof Scale;
  title: string;
  content: ReactNode;
}

const SIZING_GUIDE_SECTIONS: GuideSection[] = [
  {
    id: "overview",
    icon: Scale,
    title: "什么是仓位管理",
    content: (
      <div className="space-y-2 text-xs text-muted-foreground">
        <p>仓位管理模块位于<strong>信号</strong>和<strong>执行</strong>之间，回答一个核心问题：</p>
        <p className="text-sm font-medium text-foreground text-center py-1">拿到买/卖信号后，该下多少仓位？</p>
        <div className="bg-muted/40 rounded p-2 text-[10px] font-mono leading-relaxed">
          {`信号层  strategy.generate() → [-1, 1]
分配层  optimizer (可选) → 调整权重
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
仓位层  position_sizer → 风控约束
执行层  _rebalance() → 实际下单`}
        </div>
        <p>所有计算基于<strong>当前实时权益</strong>，赢了自动放大，亏了自动缩小。</p>
      </div>
    ),
  },
  {
    id: "sizers",
    icon: Target,
    title: "可用的仓位计算器",
    content: (
      <div className="space-y-2 text-xs text-muted-foreground">
        <table className="w-full text-[10px]">
          <thead><tr className="border-b"><th className="py-1 text-left text-foreground">类型</th><th className="py-1 text-left text-foreground">原理</th></tr></thead>
          <tbody>
            <tr className="border-b border-muted/30">
              <td className="py-1.5 font-medium text-foreground">fixed_fractional</td>
              <td className="py-1.5">每笔最多亏 X% 权益<br/><span className="text-primary/80">推荐入门使用，最经典的方法</span></td>
            </tr>
            <tr className="border-b border-muted/30">
              <td className="py-1.5 font-medium text-foreground">atr_sizing</td>
              <td className="py-1.5">海龟交易法，按 ATR 波动率定仓位<br/>波动大→仓位小，波动小→仓位大</td>
            </tr>
            <tr>
              <td className="py-1.5 font-medium text-foreground">equal_weight</td>
              <td className="py-1.5">等权重 1/N 分配<br/>朴素但稳健，常用作基线对照</td>
            </tr>
          </tbody>
        </table>
        <p className="text-[10px] text-primary/80">多个 sizer 可以串联使用，Pipeline 依次调整权重</p>
      </div>
    ),
  },
  {
    id: "stops",
    icon: Shield,
    title: "止损/止盈管理",
    content: (
      <div className="space-y-2 text-xs text-muted-foreground">
        <table className="w-full text-[10px]">
          <thead><tr className="border-b"><th className="py-1 text-left text-foreground">类型</th><th className="py-1 text-left text-foreground">说明</th></tr></thead>
          <tbody>
            <tr className="border-b border-muted/30">
              <td className="py-1.5 font-medium text-foreground">atr_stop</td>
              <td className="py-1.5">入场价 ± N×ATR，最常用</td>
            </tr>
            <tr className="border-b border-muted/30">
              <td className="py-1.5 font-medium text-foreground">fixed_pct</td>
              <td className="py-1.5">入场价 × (1 - X%)</td>
            </tr>
            <tr className="border-b border-muted/30">
              <td className="py-1.5 font-medium text-foreground">trailing_pct</td>
              <td className="py-1.5">跟踪最高价，回落 X% 止损</td>
            </tr>
            <tr className="border-b border-muted/30">
              <td className="py-1.5 font-medium text-foreground">time_exit</td>
              <td className="py-1.5">持仓 N 根 K 线后强制平仓</td>
            </tr>
            <tr>
              <td className="py-1.5 font-medium text-foreground">profit_target</td>
              <td className="py-1.5">入场价 × (1 + X%) 止盈</td>
            </tr>
          </tbody>
        </table>
        <p className="text-[10px]">K 线图上的<span className="text-red-500 font-medium">红色虚线</span>是止损线，<span className="text-green-500 font-medium">绿色虚线</span>是止盈线</p>
      </div>
    ),
  },
  {
    id: "config",
    icon: Zap,
    title: "配置方法",
    content: (
      <div className="space-y-2 text-xs text-muted-foreground">
        <p>在 backtest config.json 中添加 <code className="bg-muted/60 px-1 rounded">position_sizing</code> 字段：</p>
        <pre className="bg-muted/40 rounded p-2 text-[10px] overflow-x-auto leading-relaxed">{`{
  "initial_cash": 100000,
  "position_sizing": {
    "sizers": [
      {
        "type": "fixed_fractional",
        "risk_per_trade": 0.02,
        "max_position_pct": 0.25
      }
    ],
    "stops": {
      "type": "atr_stop",
      "atr_multiplier": 2.0,
      "trailing": true,
      "profit_target_pct": 0.20
    }
  }
}`}</pre>
        <p className="text-[10px] text-primary/80">不配置 position_sizing 时回测行为完全不变</p>
      </div>
    ),
  },
  {
    id: "params",
    icon: TrendingUp,
    title: "参数详解",
    content: (
      <div className="space-y-3 text-xs text-muted-foreground">
        <div>
          <p className="font-medium text-foreground mb-1">fixed_fractional 参数</p>
          <table className="w-full text-[10px]">
            <tbody>
              <tr className="border-b border-muted/30"><td className="py-1 font-mono">risk_per_trade</td><td className="py-1">每笔风险占比，默认 0.02 (2%)</td></tr>
              <tr className="border-b border-muted/30"><td className="py-1 font-mono">max_position_pct</td><td className="py-1">单品种上限，默认 0.25 (25%)</td></tr>
              <tr><td className="py-1 font-mono">default_stop_pct</td><td className="py-1">无止损时的假设风险距离，默认 0.05</td></tr>
            </tbody>
          </table>
        </div>
        <div>
          <p className="font-medium text-foreground mb-1">atr_sizing 参数</p>
          <table className="w-full text-[10px]">
            <tbody>
              <tr className="border-b border-muted/30"><td className="py-1 font-mono">atr_period</td><td className="py-1">ATR 计算周期，默认 20</td></tr>
              <tr className="border-b border-muted/30"><td className="py-1 font-mono">risk_per_unit</td><td className="py-1">每 ATR 单位风险，默认 0.01 (1%)</td></tr>
              <tr><td className="py-1 font-mono">max_position_pct</td><td className="py-1">单品种上限，默认 0.25</td></tr>
            </tbody>
          </table>
        </div>
        <div>
          <p className="font-medium text-foreground mb-1">atr_stop 参数</p>
          <table className="w-full text-[10px]">
            <tbody>
              <tr className="border-b border-muted/30"><td className="py-1 font-mono">atr_multiplier</td><td className="py-1">ATR 倍数，默认 2.0</td></tr>
              <tr className="border-b border-muted/30"><td className="py-1 font-mono">trailing</td><td className="py-1">是否移动止损，默认 false</td></tr>
              <tr className="border-b border-muted/30"><td className="py-1 font-mono">profit_target_pct</td><td className="py-1">止盈百分比，如 0.20 (20%)</td></tr>
              <tr><td className="py-1 font-mono">time_exit_bars</td><td className="py-1">最大持仓K线数</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    ),
  },
  {
    id: "presets",
    icon: Zap,
    title: "推荐预设",
    content: (
      <div className="space-y-3 text-xs text-muted-foreground">
        <div>
          <p className="font-medium text-foreground mb-1">🛡️ 保守型（新手推荐）</p>
          <pre className="bg-muted/40 rounded p-1.5 text-[10px] leading-relaxed">{`risk_per_trade: 0.01  (每笔亏≤1%)
max_position_pct: 0.20
stop: atr_stop, multiplier=3.0`}</pre>
        </div>
        <div>
          <p className="font-medium text-foreground mb-1">⚖️ 均衡型</p>
          <pre className="bg-muted/40 rounded p-1.5 text-[10px] leading-relaxed">{`risk_per_trade: 0.02  (每笔亏≤2%)
max_position_pct: 0.25
stop: atr_stop, multiplier=2.0
trailing: true`}</pre>
        </div>
        <div>
          <p className="font-medium text-foreground mb-1">🔥 激进型</p>
          <pre className="bg-muted/40 rounded p-1.5 text-[10px] leading-relaxed">{`risk_per_trade: 0.05  (每笔亏≤5%)
max_position_pct: 0.40
stop: atr_stop, multiplier=1.5`}</pre>
        </div>
        <p className="text-[10px] text-primary/80">以 10 万为例：保守每笔亏≤1000，均衡≤2000，激进≤5000</p>
      </div>
    ),
  },
  {
    id: "cashflow",
    icon: TrendingUp,
    title: "资金注入/提取",
    content: (
      <div className="space-y-2 text-xs text-muted-foreground">
        <p>回测中模拟中途加/减资金：</p>
        <pre className="bg-muted/40 rounded p-2 text-[10px] overflow-x-auto leading-relaxed">{`"cash_flows": [
  {"date":"2024-06-01","amount": 30000},
  {"date":"2024-09-01","amount":-20000}
]`}</pre>
        <p>注入后 sizing 基于新权益重新计算，自动适应资金变化。</p>
      </div>
    ),
  },
  {
    id: "metrics",
    icon: BarChart3,
    title: "新增指标说明",
    content: (
      <div className="space-y-2 text-xs text-muted-foreground">
        <table className="w-full text-[10px]">
          <thead><tr className="border-b"><th className="py-1 text-left text-foreground">指标</th><th className="py-1 text-left text-foreground">含义</th></tr></thead>
          <tbody>
            <tr className="border-b border-muted/30"><td className="py-1">止损命中率</td><td className="py-1">止损退出次数 / 总退出次数，越低越好</td></tr>
            <tr className="border-b border-muted/30"><td className="py-1">最大单笔亏损</td><td className="py-1">最大一笔亏损占初始资金比例</td></tr>
            <tr className="border-b border-muted/30"><td className="py-1">止损/止盈次数</td><td className="py-1">各退出原因触发的次数</td></tr>
          </tbody>
        </table>
        <p>交易表格中的退出原因标签颜色：</p>
        <div className="flex flex-wrap gap-1 mt-1">
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">止损</span>
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400">移动止损</span>
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">止盈</span>
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">超时</span>
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">减仓</span>
        </div>
      </div>
    ),
  },
];

export function PositionSizingGuide({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  useFocusTrap(panelRef, open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="position-sizing-guide-title"
        className="w-full max-w-md h-full bg-background border-l shadow-xl overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-background/95 backdrop-blur-sm border-b px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Scale className="h-4 w-4 text-primary" />
            <span id="position-sizing-guide-title" className="font-semibold text-sm">仓位管理指南</span>
          </div>
          <button type="button" onClick={onClose} className="inline-flex h-11 w-11 items-center justify-center rounded hover:bg-muted" aria-label="关闭仓位管理指南"><X className="h-4 w-4" aria-hidden="true" /></button>
        </div>
        <div className="p-4 space-y-2">
          {SIZING_GUIDE_SECTIONS.map((section) => (
            <div key={section.id} className="border rounded-lg overflow-hidden">
              <button
                onClick={() => setExpandedId(expandedId === section.id ? null : section.id)}
                className="w-full px-3 py-2.5 flex items-center gap-2 text-sm font-medium hover:bg-muted/40 transition"
              >
                <section.icon className="h-4 w-4 text-primary shrink-0" />
                <span className="flex-1 text-left">{section.title}</span>
              </button>
              {expandedId === section.id && (
                <div className="px-3 pb-3">{section.content}</div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
