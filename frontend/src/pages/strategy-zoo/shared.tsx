import { useState } from "react";
import { Shield, ShieldAlert, ShieldCheck, X, ChevronRight, ChevronDown, Terminal, Lightbulb, Filter, MousePointer, Code2, BarChart3, BookOpen } from "lucide-react";
import { cn } from "@/lib/utils";

/* ---------- Constants ---------- */

export interface CategoryCard {
  id: string;
  title: string;
  titleZh: string;
  description: string;
  accent: string;
}

export const CATEGORY_CARDS: CategoryCard[] = [
  {
    id: "trend",
    title: "Trend Following",
    titleZh: "趋势跟踪",
    description: "MA crossover, Donchian breakout, Turtle, ADX, SuperTrend",
    accent: "from-blue-500/20 to-blue-500/5",
  },
  {
    id: "mean_reversion",
    title: "Mean Reversion",
    titleZh: "均值回归",
    description: "Bollinger bands, RSI reversal, Z-Score, OU process",
    accent: "from-violet-500/20 to-violet-500/5",
  },
  {
    id: "momentum",
    title: "Momentum",
    titleZh: "动量策略",
    description: "Cross-sectional, TSMOM, 52-week high, dual momentum",
    accent: "from-amber-500/20 to-amber-500/5",
  },
  {
    id: "multi_factor",
    title: "Multi-Factor",
    titleZh: "多因子",
    description: "Quality+Value, fundamental, factor rotation, Alpha combo",
    accent: "from-emerald-500/20 to-emerald-500/5",
  },
  {
    id: "stat_arb",
    title: "Statistical Arbitrage",
    titleZh: "统计套利",
    description: "Cointegration pair, market-neutral, AH premium, ETF arb",
    accent: "from-indigo-500/20 to-indigo-500/5",
  },
  {
    id: "event_driven",
    title: "Event Driven",
    titleZh: "事件驱动",
    description: "PEAD, dragon-tiger board, northbound flow, limit-up",
    accent: "from-red-500/20 to-red-500/5",
  },
  {
    id: "volatility",
    title: "Volatility",
    titleZh: "波动率",
    description: "Volatility breakout, GARCH proxy, variance risk premium",
    accent: "from-pink-500/20 to-pink-500/5",
  },
  {
    id: "allocation",
    title: "Allocation",
    titleZh: "组合配置",
    description: "Risk parity, All-Weather, Black-Litterman, Kelly",
    accent: "from-teal-500/20 to-teal-500/5",
  },
  {
    id: "crypto",
    title: "Crypto",
    titleZh: "加密货币",
    description: "Funding rate arb, grid trading, DeFi yield, on-chain",
    accent: "from-orange-500/20 to-orange-500/5",
  },
  {
    id: "options",
    title: "Options",
    titleZh: "期权策略",
    description: "Covered call, iron condor, vol smile, put-write",
    accent: "from-gray-500/20 to-gray-500/5",
  },
];

export const UNIVERSE_OPTIONS = [
  { value: "equity_cn", label: "A股 (China A)" },
  { value: "equity_us", label: "美股 (US Equity)" },
  { value: "equity_hk", label: "港股 (HK Equity)" },
  { value: "crypto", label: "加密货币 (Crypto)" },
  { value: "futures", label: "期货 (Futures)" },
];

export const RISK_OPTIONS = [
  { value: "low", label: "低风险 (Low)" },
  { value: "medium", label: "中风险 (Medium)" },
  { value: "high", label: "高风险 (High)" },
];

export const PAGE_SIZE = 50;

/* ---------- Helpers ---------- */

export function metaString(meta: Record<string, unknown>, key: string): string {
  const v = meta[key];
  if (v === undefined || v === null || v === "") return "—";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function RiskBadge({ risk }: { risk: string }) {
  const tone =
    risk === "low"
      ? "bg-green-500/10 text-green-700 dark:text-green-300"
      : risk === "high"
        ? "bg-red-500/10 text-red-700 dark:text-red-300"
        : "bg-amber-500/10 text-amber-700 dark:text-amber-300";
  const Icon = risk === "low" ? ShieldCheck : risk === "high" ? ShieldAlert : Shield;
  return (
    <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium", tone)}>
      <Icon className="h-3 w-3" aria-hidden="true" />
      {risk}
    </span>
  );
}

export function CategoryBadge({ category }: { category: string }) {
  const card = CATEGORY_CARDS.find((c) => c.id === category);
  return (
    <span className="inline-block px-2 py-0.5 rounded-full bg-primary/10 text-primary text-[10px] font-medium">
      {card?.titleZh || category}
    </span>
  );
}

/* ---------- Guide sections ---------- */

interface GuideSection {
  id: string;
  icon: typeof BookOpen;
  title: string;
  content: React.ReactNode;
}

const GUIDE_SECTIONS: GuideSection[] = [
  {
    id: "cards",
    icon: MousePointer,
    title: "分类卡片",
    content: (
      <div className="space-y-2 text-xs text-muted-foreground">
        <p>页面顶部 <strong>10 个分类卡片</strong>，每个显示分类名称和策略数量。</p>
        <p><strong>点击卡片</strong> 快速筛选该分类策略，再次点击取消筛选。</p>
        <div className="grid grid-cols-2 gap-1 mt-2">
          {[
            ["趋势跟踪", "5"], ["均值回归", "4"], ["动量策略", "4"], ["多因子", "4"],
            ["统计套利", "4"], ["事件驱动", "4"], ["波动率", "3"], ["组合配置", "4"],
            ["加密货币", "4"], ["期权策略", "4"],
          ].map(([name, n]) => (
            <span key={name} className="text-[10px] px-1.5 py-0.5 rounded bg-muted/50">{name} ({n})</span>
          ))}
        </div>
      </div>
    ),
  },
  {
    id: "filters",
    icon: Filter,
    title: "筛选器",
    content: (
      <div className="space-y-2 text-xs text-muted-foreground">
        <p>四维筛选，可任意组合：</p>
        <table className="w-full text-[10px]">
          <tbody>
            <tr className="border-b border-muted/30"><td className="py-1 font-medium text-foreground">Search</td><td className="py-1">按 ID / 中文昵称 / 描述模糊搜索</td></tr>
            <tr className="border-b border-muted/30"><td className="py-1 font-medium text-foreground">Category</td><td className="py-1">下拉选择分类（与卡片联动）</td></tr>
            <tr className="border-b border-muted/30"><td className="py-1 font-medium text-foreground">Universe</td><td className="py-1">按市场筛选：A股/美股/港股/加密/期货</td></tr>
            <tr><td className="py-1 font-medium text-foreground">Risk</td><td className="py-1">按风险等级：low(10) / medium(20) / high(10)</td></tr>
          </tbody>
        </table>
        <p className="text-[10px] text-primary/80">💡 搜索支持中英文，输入「海龟」可匹配 trend_turtle</p>
      </div>
    ),
  },
  {
    id: "table",
    icon: BarChart3,
    title: "策略列表",
    content: (
      <div className="space-y-2 text-xs text-muted-foreground">
        <p>表格展示所有匹配的策略：</p>
        <table className="w-full text-[10px]">
          <tbody>
            <tr className="border-b border-muted/30"><td className="py-1 font-medium text-foreground">ID</td><td className="py-1">点击进入详情页，右侧显示中文昵称</td></tr>
            <tr className="border-b border-muted/30"><td className="py-1 font-medium text-foreground">Category</td><td className="py-1">彩色分类标签</td></tr>
            <tr className="border-b border-muted/30"><td className="py-1 font-medium text-foreground">Universe</td><td className="py-1">适用市场列表</td></tr>
            <tr className="border-b border-muted/30"><td className="py-1 font-medium text-foreground">Risk</td><td className="py-1">🟢low / 🟡medium / 🔴high 徽章</td></tr>
            <tr><td className="py-1 font-medium text-foreground">Reference</td><td className="py-1">学术/经典来源</td></tr>
          </tbody>
        </table>
      </div>
    ),
  },
  {
    id: "detail",
    icon: Code2,
    title: "详情页",
    content: (
      <div className="space-y-2 text-xs text-muted-foreground">
        <p>点击策略 ID 进入详情页，包含：</p>
        <ul className="list-disc pl-4 space-y-1">
          <li><strong>标题区</strong>：策略 ID + 分类标签 + 风险徽章 + 中英文描述</li>
          <li><strong>元数据表</strong>：Category / Universe / Frequency / Risk / Min bars / Columns / Default params / Factors used / Reference</li>
          <li><strong>源码查看</strong>：点击「View source」展开完整 Python 源码</li>
        </ul>
        <p className="text-[10px] text-primary/80">💡 点击「← Back to Strategy Zoo」返回浏览页</p>
      </div>
    ),
  },
  {
    id: "recommend",
    icon: Lightbulb,
    title: "按市场推荐",
    content: (
      <div className="space-y-3 text-xs text-muted-foreground">
        <div>
          <p className="font-medium text-foreground mb-1">🇨🇳 A股</p>
          <p className="text-[10px]">低风险：risk_parity + quality_value + bollinger</p>
          <p className="text-[10px]">高风险：dragon_tiger + limit_board + northbound</p>
        </div>
        <div>
          <p className="font-medium text-foreground mb-1">🇺🇸 美股</p>
          <p className="text-[10px]">低风险：all_weather + covered_call + market_neutral</p>
          <p className="text-[10px]">中风险：tsmom + black_litterman + iron_condor</p>
        </div>
        <div>
          <p className="font-medium text-foreground mb-1">₿ 加密</p>
          <p className="text-[10px]">funding_arb + grid + turtle</p>
        </div>
      </div>
    ),
  },
  {
    id: "api",
    icon: Terminal,
    title: "API / Python",
    content: (
      <div className="space-y-2 text-xs text-muted-foreground">
        <p className="font-medium text-foreground">REST API</p>
        <pre className="bg-muted/40 rounded p-2 text-[10px] overflow-x-auto leading-relaxed">{`GET /strategy/list
GET /strategy/list?category=trend
GET /strategy/list?universe=equity_cn&risk=low
GET /strategy/{strategy_id}`}</pre>
        <p className="font-medium text-foreground mt-2">Python</p>
        <pre className="bg-muted/40 rounded p-2 text-[10px] overflow-x-auto leading-relaxed">{`from src.strategies.runner import run

result = run(
  "trend_dual_ma",
  codes=["000001.SZ"],
  params={"fast_period": 10},
)`}</pre>
      </div>
    ),
  },
];

/* ---------- Guide drawer ---------- */

export function GuideDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(["cards"]));

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/30 z-40 lg:hidden"
          onClick={onClose}
        />
      )}
      {/* Drawer */}
      <aside
        className={cn(
          "fixed top-0 right-0 h-full w-80 bg-card border-l z-50 flex flex-col transition-transform duration-300 ease-in-out",
          open ? "translate-x-0" : "translate-x-full",
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b shrink-0">
          <div className="flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">操作指南</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-muted transition-colors"
            title="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-3 space-y-1">
          {GUIDE_SECTIONS.map((sec) => {
            const isOpen = expanded.has(sec.id);
            const Icon = sec.icon;
            return (
              <div key={sec.id} className="border rounded-lg overflow-hidden">
                <button
                  type="button"
                  onClick={() => toggle(sec.id)}
                  className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-muted/40 transition-colors"
                >
                  <Icon className="h-3.5 w-3.5 text-primary shrink-0" />
                  <span className="text-xs font-medium flex-1">{sec.title}</span>
                  {isOpen ? (
                    <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                  )}
                </button>
                {isOpen && (
                  <div className="px-3 pb-3 border-t">
                    <div className="pt-2">{sec.content}</div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div className="border-t px-4 py-2 text-[10px] text-muted-foreground/60 shrink-0">
          Strategy Zoo · 40 strategies · 10 categories
        </div>
      </aside>
    </>
  );
}

/* ---------- Page entry ---------- */
