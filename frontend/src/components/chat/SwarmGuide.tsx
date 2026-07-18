import { memo, useRef, useState } from "react";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Layers,
  Search,
  Settings,
  Shield,
  Sparkles,
  Users,
  X,
  Zap,
} from "lucide-react";

interface Props {
  open: boolean;
  onClose: () => void;
  onSelectPreset?: (command: string) => void;
}

interface SectionProps {
  id: string;
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}

function Section({ icon, title, children, defaultOpen = false }: SectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-muted/50 transition-colors"
      >
        <span className="text-primary">{icon}</span>
        <span className="flex-1 text-sm font-semibold text-foreground">{title}</span>
        {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && <div className="px-4 pb-4 text-sm text-foreground/80 leading-relaxed space-y-3">{children}</div>}
    </div>
  );
}

const PRESETS = [
  { cat: "投资决策", items: [
    { name: "investment_committee", title: "投资委员会", dag: "Bull ⊥ Bear → CRO → PM", desc: "个股买卖决策" },
    { name: "full_investment_pipeline", title: "完整投研流水线", dag: "4分析师 → Bull⊥Bear → RM → Trader → PM", desc: "从数据到执行的完整投研" },
    { name: "portfolio_review_board", title: "组合审查", dag: "归因⊥风控⊥执行 → CIO", desc: "组合定期审查" },
    { name: "global_allocation_committee", title: "全球配置", dag: "A股⊥加密⊥美港 → 配置师", desc: "跨市场资产配置" },
  ]},
  { cat: "研究分析", items: [
    { name: "equity_research_team", title: "权益研究", dag: "宏观→行业→选股→编辑", desc: "自上而下研究" },
    { name: "fundamental_research_team", title: "基本面研究", dag: "财务⊥估值⊥质量→编辑", desc: "个股深度" },
    { name: "a_stock_deep_research", title: "A股深度", dag: "技术⊥基本面⊥情绪⊥政策⊥游资⊥解禁→编辑", desc: "A股特化多维" },
    { name: "earnings_research_desk", title: "财报研究", dag: "基本面⊥预期修正⊥期权→策略师", desc: "财报季分析" },
    { name: "universal_research_team", title: "通用模板", dag: "数据→角度A⊥角度B→综合", desc: "非标资产研究" },
  ]},
  { cat: "技术分析", items: [
    { name: "technical_analysis_panel", title: "技术面板", dag: "经典TA⊥一目⊥谐波⊥波浪⊥SMC→聚合", desc: "五学派共识" },
  ]},
  { cat: "宏观策略", items: [
    { name: "macro_strategy_forum", title: "宏观论坛", dag: "全球⊥国内⊥政策→策略师", desc: "宏观环境判断" },
    { name: "macro_rates_fx_desk", title: "利率汇率台", dag: "利率⊥外汇⊥商品→宏观PM", desc: "宏观交易" },
    { name: "geopolitical_war_room", title: "地缘作战室", dag: "地缘⊥能源⊥供应链→策略师", desc: "地缘风险分析" },
  ]},
  { cat: "情绪资金", items: [
    { name: "sentiment_intelligence_team", title: "情绪情报组", dag: "新闻⊥社交⊥资金→合成器", desc: "情绪综合评分" },
    { name: "social_alpha_team", title: "社交另类", dag: "Twitter⊥TG⊥Reddit→Alpha", desc: "社交情绪因子" },
    { name: "sector_rotation_team", title: "板块轮动", dag: "周期⊥景气⊥资金→策略师", desc: "板块轮动研判" },
    { name: "supply_chain_research_team", title: "供应链卡脖子", dag: "制图⊥证据→评分→红队→总监", desc: "供应链瓶颈研究" },
  ]},
  { cat: "加密资产", items: [
    { name: "crypto_research_lab", title: "加密研究", dag: "链上⊥DeFi⊥情绪→Alpha", desc: "加密深度研究" },
    { name: "crypto_trading_desk", title: "加密交易台", dag: "费率⊥清算⊥资金流→风控", desc: "加密仓位管理" },
  ]},
  { cat: "量化策略", items: [
    { name: "quant_strategy_desk", title: "量化策略台", dag: "筛选⊥因子→回测→风控", desc: "量化选股" },
    { name: "factor_research_committee", title: "因子研究", dag: "挖掘⊥验证→组合→回测", desc: "因子开发" },
    { name: "ml_quant_lab", title: "ML量化", dag: "特征⊥模型→回测", desc: "ML策略" },
    { name: "statistical_arbitrage_desk", title: "统计套利", dag: "扫描⊥微结构→策略→风控", desc: "配对/统计套利" },
    { name: "pairs_research_lab", title: "配对交易", dag: "相关⊥协整→策略→审查", desc: "配对交易" },
  ]},
  { cat: "固收另类", items: [
    { name: "credit_research_team", title: "信用研究", dag: "信用⊥利率⊥行业→固收", desc: "债券信用" },
    { name: "convertible_bond_team", title: "可转债", dag: "债底⊥正股⊥期权→策略师", desc: "可转债" },
    { name: "commodity_research_team", title: "商品研究", dag: "供给⊥需求→周期策略师", desc: "大宗商品" },
    { name: "derivatives_strategy_desk", title: "衍生品策略", dag: "波动率→策略→Greeks", desc: "期权策略" },
  ]},
  { cat: "风控评审", items: [
    { name: "risk_committee", title: "风险委员会", dag: "回撤⊥尾部⊥状态→首席风控", desc: "组合风险" },
    { name: "etf_allocation_desk", title: "ETF配置", dag: "筛选⊥宏观⊥风险→优化", desc: "ETF组合" },
    { name: "fund_selection_panel", title: "基金筛选", dag: "筛选→归因→FOF优化", desc: "基金/FOF" },
    { name: "global_equities_desk", title: "全球股票台", dag: "A股⊥美港⊥加密→全球策略师", desc: "跨市场研究" },
    { name: "event_driven_task_force", title: "事件驱动", dag: "扫描→影响→策略", desc: "事件投资" },
  ]},
];

const ENV_VARS = [
  { name: "SWARM_QUALITY_SCORING", def: "on", desc: "质量评分开关" },
  { name: "SWARM_CROSS_VALIDATION", def: "on", desc: "交叉检验开关" },
  { name: "SWARM_QUICK_MODEL", def: "—", desc: "分析师用快速模型" },
  { name: "SWARM_DEEP_MODEL", def: "—", desc: "决策者用深度模型" },
  { name: "SWARM_MEMORY", def: "off", desc: "跨次运行记忆" },
  { name: "SWARM_WORKER_MAX_ITER", def: "50", desc: "Worker最大迭代" },
  { name: "SWARM_WORKER_TIMEOUT", def: "300", desc: "Worker超时(秒)" },
];

export const SwarmGuide = memo(function SwarmGuide({ open, onClose, onSelectPreset }: Props) {
  const [search, setSearch] = useState("");
  const panelRef = useRef<HTMLDivElement>(null);
  useFocusTrap(panelRef, open, onClose);

  if (!open) return null;

  const q = search.toLowerCase();
  const filtered = q
    ? PRESETS.map(cat => ({
        ...cat,
        items: cat.items.filter(p =>
          p.name.includes(q) || p.title.includes(q) || p.desc.includes(q) || p.dag.includes(q) || cat.cat.includes(q)
        ),
      })).filter(cat => cat.items.length > 0)
    : PRESETS;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" onClick={onClose} />
      <div ref={panelRef} role="dialog" aria-modal="true" aria-labelledby="swarm-guide-title" className="relative w-full max-w-lg bg-background border-l shadow-2xl flex flex-col animate-in slide-in-from-right duration-200">
        {/* Header */}
        <div className="flex items-center gap-3 border-b px-4 py-3 shrink-0">
          <BookOpen className="h-5 w-5 text-primary" />
          <h2 id="swarm-guide-title" className="flex-1 text-base font-bold">Swarm 使用手册</h2>
          <button type="button" onClick={onClose} className="inline-flex h-11 w-11 items-center justify-center rounded-md hover:bg-muted transition-colors" aria-label="关闭 Swarm 使用手册">
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {/* Quick Intro */}
          <div className="px-4 py-4 bg-primary/5 border-b">
            <p className="text-sm text-foreground/80 leading-relaxed">
              <strong>Swarm</strong> 是 DAG 驱动的多 Agent 协作框架，将复杂投研任务拆解为多个 Agent 并行/串行执行。
              每个 Agent 有独立角色、工具集和技能库，由综合节点汇总各方观点产出最终报告。
            </p>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {["33 个预设团队", "实时防幻觉", "质量门控", "交叉检验", "矛盾辩论", "LLM 分层", "跨次记忆"].map(tag => (
                <span key={tag} className="px-2 py-0.5 text-[10px] rounded-full border bg-background font-medium">{tag}</span>
              ))}
            </div>
          </div>

          {/* Quick Start */}
          <Section id="quickstart" icon={<Zap className="h-4 w-4" />} title="快速开始" defaultOpen>
            <div className="space-y-2">
              <p className="font-medium text-foreground">三种使用方式：</p>
              <div className="space-y-1.5 text-xs">
                <div className="flex gap-2">
                  <span className="shrink-0 font-mono bg-muted px-1.5 py-0.5 rounded">1</span>
                  <span>点击输入框旁 <kbd className="px-1 border rounded text-[10px]">+</kbd> → <strong>Agent Swarm</strong> → 输入分析需求</span>
                </div>
                <div className="flex gap-2">
                  <span className="shrink-0 font-mono bg-muted px-1.5 py-0.5 rounded">2</span>
                  <span>直接输入 <code className="bg-muted px-1 rounded">[Swarm Team Mode] 分析贵州茅台投资价值</code></span>
                </div>
                <div className="flex gap-2">
                  <span className="shrink-0 font-mono bg-muted px-1.5 py-0.5 rounded">3</span>
                  <span>指定 preset: <code className="bg-muted px-1 rounded">[Swarm Team Mode] Use investment_committee for 600519.SH</code></span>
                </div>
              </div>
              <div className="mt-2 p-2.5 rounded-lg bg-muted/50 border text-xs space-y-1">
                <p className="font-medium text-foreground">变量填写技巧</p>
                <p><strong>target</strong> — A股 <code>600519.SH</code> / 美股 <code>NVDA.US</code> / 加密 <code>BTC-USDT</code></p>
                <p><strong>market</strong> — <code>A-shares</code> / <code>US</code> / <code>Hong Kong</code> / <code>crypto</code></p>
                <p><strong>timeframe</strong> — <code>daily</code> / <code>weekly</code> / <code>monthly</code></p>
                <p><strong>goal</strong> — 越具体越好</p>
              </div>
            </div>
          </Section>

          {/* Presets Catalog */}
          <Section id="presets" icon={<Users className="h-4 w-4" />} title={`全部预设团队 (${PRESETS.reduce((a, c) => a + c.items.length, 0)} 个)`}>
            <div className="relative mb-3">
              <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
              <input
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="搜索 preset 名称、描述..."
                className="w-full pl-8 pr-3 py-1.5 text-xs rounded-lg border bg-muted/30 focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
            <div className="space-y-3">
              {filtered.map(cat => (
                <div key={cat.cat}>
                  <p className="text-xs font-semibold text-primary mb-1">{cat.cat}</p>
                  <div className="space-y-1">
                    {cat.items.map(p => (
                      <div
                        key={p.name}
                        className={`flex items-start gap-2 p-2 rounded-lg transition-colors ${onSelectPreset ? "cursor-pointer hover:bg-primary/10 hover:ring-1 hover:ring-primary/30" : "hover:bg-muted/50"}`}
                        onClick={() => {
                          if (!onSelectPreset) return;
                          onSelectPreset(`[Swarm Team Mode] Use ${p.name}`);
                          onClose();
                        }}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs font-semibold text-foreground">{p.title}</span>
                            <code className="text-[10px] text-muted-foreground bg-muted px-1 rounded">{p.name}</code>
                            {onSelectPreset && <span className="text-[10px] text-primary/60 ml-auto">点击使用 →</span>}
                          </div>
                          <p className="text-[10px] text-muted-foreground mt-0.5">{p.desc}</p>
                          <p className="text-[10px] font-mono text-primary/70 mt-0.5">{p.dag}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
              {filtered.length === 0 && (
                <p className="text-xs text-muted-foreground text-center py-4">未找到匹配的 preset</p>
              )}
            </div>
          </Section>

          {/* How It Works */}
          <Section id="howitworks" icon={<GitBranch className="h-4 w-4" />} title="运行原理">
            <div className="space-y-3">
              <div>
                <p className="font-medium text-foreground text-xs mb-1">DAG 执行模型</p>
                <p className="text-xs">任务按拓扑排序分层执行：层内并行（最多 4 Worker），层间串行。每个 Worker 运行独立的 ReAct 循环。</p>
              </div>
              <div>
                <p className="font-medium text-foreground text-xs mb-1">Worker 三阶段</p>
                <div className="text-xs space-y-0.5">
                  <p>① <strong>规划</strong> — 3-5 要点分析计划（不调工具）</p>
                  <p>② <strong>执行</strong> — load_skill → 写脚本 → 执行（≤15 次工具调用）</p>
                  <p>③ <strong>总结</strong> — 必须 write_file 写 report.md</p>
                </div>
              </div>
              <div>
                <p className="font-medium text-foreground text-xs mb-1">上下文传递</p>
                <p className="text-xs">上游 Agent 的 report.md 通过 <code>{"{upstream_context}"}</code> 自动注入下游 Agent 的 prompt，并附带质量评分和交叉检验结果。</p>
              </div>
              <div>
                <p className="font-medium text-foreground text-xs mb-1">数据防幻觉 (Grounding)</p>
                <p className="text-xs">自动识别标的符号 → 预取 30 天 K 线 → 注入 Agent prompt → 强制引用真实价格。
                  <strong>Agent 禁止引用训练数据中的价格。</strong></p>
              </div>
            </div>
          </Section>

          {/* Smart Enhancements */}
          <Section id="enhancements" icon={<Sparkles className="h-4 w-4" />} title="四层智能增强">
            <div className="space-y-3">
              <div className="p-2.5 rounded-lg border bg-emerald-500/5 border-emerald-500/20">
                <p className="text-xs font-semibold text-emerald-600 dark:text-emerald-400">① 自动质量评分</p>
                <p className="text-[11px] mt-1">每个 Worker 完成后自动评分 A-F。D/F 报告带反馈重跑一次。下游 Agent 看到各来源的质量评分，自动降权低质量来源。</p>
              </div>
              <div className="p-2.5 rounded-lg border bg-blue-500/5 border-blue-500/20">
                <p className="text-xs font-semibold text-blue-600 dark:text-blue-400">② 交叉检验 + 矛盾辩论</p>
                <p className="text-[11px] mt-1">Fan-in 节点（≥2 上游）自动检测矛盾、盲区、共识。高置信矛盾触发双方辩论，双方各用数据回应。综合节点必须对矛盾选边或分场景拆解。</p>
              </div>
              <div className="p-2.5 rounded-lg border bg-violet-500/5 border-violet-500/20">
                <p className="text-xs font-semibold text-violet-600 dark:text-violet-400">③ LLM 智能分层</p>
                <p className="text-[11px] mt-1">叶子节点自动用 SWARM_QUICK_MODEL（快/便宜），终端节点用 SWARM_DEEP_MODEL（强推理）。成本降 50%+，决策质量不降。</p>
              </div>
              <div className="p-2.5 rounded-lg border bg-amber-500/5 border-amber-500/20">
                <p className="text-xs font-semibold text-amber-600 dark:text-amber-400">④ 跨次运行记忆</p>
                <p className="text-[11px] mt-1">记录每次决策/结论。下次分析同一标的时，终端节点自动看到历史 context。需设 SWARM_MEMORY=on 启用。</p>
              </div>
            </div>
          </Section>

          {/* Configuration */}
          <Section id="config" icon={<Settings className="h-4 w-4" />} title="环境变量配置">
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-1 pr-2 font-medium">变量</th>
                    <th className="pb-1 pr-2 font-medium">默认</th>
                    <th className="pb-1 font-medium">说明</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {ENV_VARS.map(v => (
                    <tr key={v.name}>
                      <td className="py-1.5 pr-2 font-mono text-[10px] text-primary">{v.name}</td>
                      <td className="py-1.5 pr-2 font-mono text-[10px]">{v.def}</td>
                      <td className="py-1.5 text-[11px]">{v.desc}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-3 p-2.5 rounded-lg bg-muted/50 border text-[11px]">
              <p className="font-medium text-foreground mb-1">推荐生产配置</p>
              <pre className="font-mono text-[10px] leading-relaxed text-muted-foreground whitespace-pre-wrap">{`SWARM_QUICK_MODEL=deepseek/deepseek-v4-flash
SWARM_DEEP_MODEL=deepseek/deepseek-v4-pro
SWARM_QUALITY_SCORING=on
SWARM_CROSS_VALIDATION=on`}</pre>
            </div>
          </Section>

          {/* Preset Selection Guide */}
          <Section id="selection" icon={<Search className="h-4 w-4" />} title="Preset 选择指南">
            <div className="space-y-1.5 text-xs">
              {[
                ["个股买卖决策", "investment_committee 或 full_investment_pipeline"],
                ["A 股特化分析", "a_stock_deep_research"],
                ["市场整体方向", "macro_strategy_forum + sentiment_intelligence_team"],
                ["跨市场配置", "global_allocation_committee"],
                ["技术面分析", "technical_analysis_panel"],
                ["加密资产研究", "crypto_research_lab"],
                ["量化策略开发", "quant_strategy_desk 或 factor_research_committee"],
                ["组合风险检查", "risk_committee"],
                ["基金/FOF 构建", "fund_selection_panel"],
                ["非标准分析", "universal_research_team"],
              ].map(([need, preset]) => (
                <div key={need} className="flex gap-2 p-1.5 rounded hover:bg-muted/50">
                  <span className="text-foreground font-medium shrink-0">{need}</span>
                  <span className="text-muted-foreground">→</span>
                  <code className="text-primary text-[10px]">{preset}</code>
                </div>
              ))}
            </div>
          </Section>

          {/* Troubleshooting */}
          <Section id="troubleshoot" icon={<Shield className="h-4 w-4" />} title="故障排查">
            <div className="space-y-2 text-xs">
              {[
                { q: "运行一直是 running", a: "查看运行详情会自动 reconcile；或手动 cancel 后 retry" },
                { q: "Agent 报告全是 D/F 质量", a: "检查数据工具是否可用（Tushare 需要 token、网络需可达）" },
                { q: "交叉检验矛盾不合理", a: "不同时间维度的分析不算矛盾；可临时 SWARM_CROSS_VALIDATION=off" },
                { q: "Token 用量过高", a: "启用 LLM 分层 (QUICK+DEEP)；减少 max_iterations" },
                { q: "下游 Agent 被 blocked", a: "上游 Agent 失败导致；修复后 retry 整次运行" },
              ].map(({ q, a }) => (
                <div key={q} className="p-2 rounded-lg bg-muted/30">
                  <p className="font-medium text-foreground">{q}</p>
                  <p className="text-muted-foreground mt-0.5">{a}</p>
                </div>
              ))}
            </div>
          </Section>

          {/* Persistence */}
          <Section id="persistence" icon={<Layers className="h-4 w-4" />} title="持久化与审计">
            <div className="text-xs space-y-2">
              <pre className="font-mono text-[10px] p-2 rounded-lg bg-muted/50 border leading-relaxed whitespace-pre-wrap">{`.swarm/runs/{run_id}/
├── run.json          # 运行状态 + token 用量
├── events.jsonl      # 完整事件时间线
├── tasks/*.json      # 各任务实时状态
└── artifacts/
    └── {agent}/
        ├── report.md       # Agent 报告
        ├── summary.md      # 摘要
        └── messages.jsonl  # 完整对话记录`}</pre>
              <p>每次运行的所有事件、报告、工具调用记录完整保存。events.jsonl 是 append-only 的，支持基于偏移量的 SSE 断点续传。</p>
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
});
