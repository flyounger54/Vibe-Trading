/**
 * Industry Chain dashboard.
 *
 * A structured, persistent veneer over the `industry_chain_dashboard` swarm:
 * create a chain (from template or custom), launch the multi-agent analysis,
 * and browse the result in a tabbed layout (Overview + per-segment + RedTeam).
 *
 * The page is a thin shell — state + tab routing — delegating each view to a
 * component under `components/industry-chain/`.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Play, Loader2, RefreshCw, GitBranch, Clock, GitCompare, Download, XCircle, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type Chain,
  type ChainSummary,
  type ChainTemplate,
} from "@/lib/api";
import { ChainSelector } from "@/components/industry-chain/ChainSelector";
import { ChainOverview } from "@/components/industry-chain/ChainOverview";
import { SegmentDetail } from "@/components/industry-chain/SegmentDetail";
import { RedTeamLog } from "@/components/industry-chain/RedTeamLog";
import { AnalysisProgress } from "@/components/industry-chain/AnalysisProgress";
import { SwarmInsight } from "@/components/industry-chain/SwarmInsight";
import { ChainCompare } from "@/components/industry-chain/ChainCompare";
import { HypothesisPanel } from "@/components/industry-chain/HypothesisPanel";

type Tab = "overview" | "redteam" | "hypotheses" | string; // string = segment_id

export function IndustryChain() {
  const navigate = useNavigate();
  const { chainId } = useParams();

  const [chains, setChains] = useState<ChainSummary[]>([]);
  const [templates, setTemplates] = useState<ChainTemplate[]>([]);
  const [chain, setChain] = useState<Chain | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [showCompare, setShowCompare] = useState(false);
  const [loading, setLoading] = useState(false);

  const loadList = useCallback(async () => {
    try {
      const [c, t] = await Promise.all([api.listChains(), api.listChainTemplates()]);
      setChains(c.chains);
      setTemplates(t.templates);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载失败");
    }
  }, []);

  const loadChain = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const c = await api.getChain(id);
      setChain(c);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载失败");
      setChain(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    if (chainId) {
      loadChain(chainId);
      setTab("overview");
    } else {
      setChain(null);
    }
  }, [chainId, loadChain]);

  const selectChain = (id: string) => navigate(`/industry-chain/${id}`);

  const onCreated = async (id: string) => {
    await loadList();
    navigate(`/industry-chain/${id}`);
  };

  const onDeleted = async (id: string) => {
    await loadList();
    if (chain?.chain_id === id) {
      setChain(null);
      navigate("/industry-chain");
    }
  };

  const startAnalysis = async () => {
    if (!chain) return;
    try {
      const result = await api.analyzeChain(chain.chain_id, undefined, crypto.randomUUID());
      toast.success(result.status === "queued" ? "分析已进入可靠任务队列" : "已启动多Agent分析");
      await loadChain(chain.chain_id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "启动失败");
    }
  };

  const cancelAnalysis = async () => {
    if (!chain) return;
    try {
      await api.cancelChainAnalysis(chain.chain_id);
      toast.success("已取消分析");
      await loadChain(chain.chain_id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "取消失败");
    }
  };

  const retryAnalysis = async () => {
    if (!chain) return;
    try {
      const result = await api.retryChainAnalysis(chain.chain_id);
      toast.success(result.status === "queued" ? "重试已进入队列" : "已重新启动分析");
      await loadChain(chain.chain_id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "重试失败");
    }
  };

  const onAnalysisDone = useCallback(() => {
    if (chain) {
      loadChain(chain.chain_id);
      loadList();
    }
  }, [chain, loadChain, loadList]);

  return (
    <div className="flex h-full flex-col gap-6 p-6">
      <div className="flex flex-col gap-2 border-b pb-4">
        <div className="flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-primary" />
          <h1 className="text-xl font-semibold">产业链看板</h1>
        </div>
        <p className="text-sm text-muted-foreground">
          看链不看股：拆环节 → 沿链比 → 找卡口。多Agent 协作（卡脖子评分 · 红队挑战 · 门控质量），一键生成结构化产业链研究。
        </p>
      </div>

      <div className="flex flex-1 gap-6 overflow-hidden">
        {/* Left rail */}
        <aside className="w-64 shrink-0 overflow-y-auto">
          <ChainSelector
            chains={chains}
            templates={templates}
            activeChainId={chain?.chain_id ?? null}
            onSelect={selectChain}
            onCreated={onCreated}
            onDeleted={onDeleted}
          />
        </aside>

        {/* Main */}
        <main className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex h-40 items-center justify-center text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : showCompare ? (
            <div className="p-2">
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <GitCompare className="h-5 w-5 text-primary" />
                  <h2 className="text-lg font-semibold">跨链对比</h2>
                </div>
                <button onClick={() => setShowCompare(false)} className="text-sm text-muted-foreground hover:text-foreground">
                  返回
                </button>
              </div>
              <ChainCompare chains={chains} />
            </div>
          ) : !chain ? (
            <div className="flex h-full flex-col items-center justify-center gap-4 text-muted-foreground">
              <GitBranch className="h-10 w-10 opacity-40" />
              <p className="text-sm">从左侧选择或新建一条产业链开始</p>
              {chains.length >= 2 && (
                <button
                  onClick={() => setShowCompare(true)}
                  className="inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-sm transition hover:bg-muted"
                >
                  <GitCompare className="h-4 w-4" /> 跨链对比
                </button>
              )}
            </div>
          ) : (
            <ChainView
              chain={chain}
              tab={tab}
              setTab={setTab}
              onAnalyze={startAnalysis}
              onCancel={cancelAnalysis}
              onRetry={retryAnalysis}
              onAnalysisDone={onAnalysisDone}
              onRefresh={() => loadChain(chain.chain_id)}
            />
          )}
        </main>
      </div>
    </div>
  );
}

function ChainView({
  chain,
  tab,
  setTab,
  onAnalyze,
  onCancel,
  onRetry,
  onAnalysisDone,
  onRefresh,
}: {
  chain: Chain;
  tab: Tab;
  setTab: (t: Tab) => void;
  onAnalyze: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onAnalysisDone: () => void;
  onRefresh: () => void;
}) {
  const analyzing = chain.status === "analyzing";
  const activeSegment = chain.segments.find((s) => s.segment_id === tab);

  return (
    <div className="space-y-5">
      {/* Header actions */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{chain.name}</h2>
          {chain.description && (
            <p className="text-sm text-muted-foreground">{chain.description}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <ScheduleSelect chainId={chain.chain_id} current={chain.refresh_schedule} rowVersion={chain.row_version} onChanged={onRefresh} />
          <ExportButton chainId={chain.chain_id} chainName={chain.name} />
          <button
            onClick={onRefresh}
            className="inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-sm transition hover:bg-muted"
          >
            <RefreshCw className="h-4 w-4" /> 刷新
          </button>
          <button
            onClick={onAnalyze}
            disabled={analyzing}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
          >
            {analyzing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            一键分析
          </button>
          {analyzing && (
            <button onClick={onCancel} className="inline-flex items-center gap-1.5 rounded-md border border-red-500/40 px-3 py-2 text-sm text-red-600 transition hover:bg-red-500/5">
              <XCircle className="h-4 w-4" /> 取消
            </button>
          )}
          {chain.status === "error" && (
            <button onClick={onRetry} className="inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-sm transition hover:bg-muted">
              <RotateCcw className="h-4 w-4" /> 重试
            </button>
          )}
        </div>
      </div>

      {chain.status === "error" && chain.last_error && (
        <div role="alert" className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-700 dark:text-red-300">
          {chain.last_error}
        </div>
      )}

      {analyzing && (
        <AnalysisProgress chainId={chain.chain_id} onDone={onAnalysisDone} />
      )}

      {chain.swarm_run_id && (
        <SwarmInsight chainId={chain.chain_id} runId={chain.swarm_run_id} />
      )}

      {/* Tabs */}
      <div className="flex flex-wrap gap-1 border-b">
        <TabButton active={tab === "overview"} onClick={() => setTab("overview")}>
          总览
        </TabButton>
        {chain.segments.map((s) => (
          <TabButton key={s.segment_id} active={tab === s.segment_id} onClick={() => setTab(s.segment_id)}>
            {s.name}
            {s.chokepoint_total != null && s.evidence_state === "supported" && (
              <span className="ml-1 text-xs text-muted-foreground">{s.chokepoint_total}</span>
            )}
          </TabButton>
        ))}
        <TabButton active={tab === "redteam"} onClick={() => setTab("redteam")}>
          红队记录
        </TabButton>
        <TabButton active={tab === "hypotheses"} onClick={() => setTab("hypotheses")}>
          假说
        </TabButton>
      </div>

      {/* Tab content */}
      <div>
        {tab === "overview" && <ChainOverview chain={chain} />}
        {tab === "redteam" && <RedTeamLog chain={chain} />}
        {tab === "hypotheses" && <HypothesisPanel chainId={chain.chain_id} />}
        {activeSegment && <SegmentDetail segment={activeSegment} />}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition ${
        active
          ? "border-primary text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

function ScheduleSelect({
  chainId,
  current,
  rowVersion,
  onChanged,
}: {
  chainId: string;
  current: string;
  rowVersion: number;
  onChanged: () => void;
}) {
  const handleChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    try {
      await api.setChainSchedule(chainId, e.target.value, rowVersion);
      toast.success(e.target.value ? `已设为${e.target.value === "weekly" ? "每周" : "每月"}自动刷新` : "已关闭定时刷新");
      onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "设置失败");
    }
  };
  return (
    <div className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-sm text-muted-foreground">
      <Clock className="h-4 w-4" />
      <select
        value={current}
        onChange={handleChange}
        className="bg-transparent text-sm outline-none"
      >
        <option value="">不自动刷新</option>
        <option value="weekly">每周</option>
        <option value="monthly">每月</option>
      </select>
    </div>
  );
}

function ExportButton({ chainId, chainName }: { chainId: string; chainName: string }) {
  const [exporting, setExporting] = useState(false);
  const handleExport = async () => {
    setExporting(true);
    try {
      const res = await api.exportChain(chainId);
      const blob = new Blob([res.markdown], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = res.filename || `${chainName}_研报.md`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("研报已导出");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "导出失败");
    } finally {
      setExporting(false);
    }
  };
  return (
    <button
      onClick={handleExport}
      disabled={exporting}
      className="inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-sm transition hover:bg-muted disabled:opacity-50"
    >
      {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
      导出
    </button>
  );
}
