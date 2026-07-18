import { useEffect, useState } from "react";
import { Lightbulb, Plus, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api, type ChainHypothesis } from "@/lib/api";
import { ErrorRetryBanner } from "@/components/common/ErrorRetryBanner";

interface Props {
  chainId: string;
}

const STATUS_STYLE: Record<string, { cls: string; label: string }> = {
  exploring: { cls: "bg-blue-500/15 text-blue-600 dark:text-blue-400", label: "探索中" },
  testing: { cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400", label: "验证中" },
  validated: { cls: "bg-green-500/15 text-green-600 dark:text-green-400", label: "已验证" },
  rejected: { cls: "bg-red-500/15 text-red-600 dark:text-red-400", label: "已否决" },
  monitoring: { cls: "bg-purple-500/15 text-purple-600 dark:text-purple-400", label: "监控中" },
};

export function HypothesisPanel({ chainId }: Props) {
  const [hypotheses, setHypotheses] = useState<ChainHypothesis[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const load = () => {
    setLoading(true);
    setError(null);
    api
      .listChainHypotheses(chainId)
      .then((r) => setHypotheses(r.hypotheses))
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "投资假说加载失败"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, [chainId]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Lightbulb className="h-4 w-4" />
          投资假说：注册可证伪的投研观点，追踪验证状态，挂载回测结果。
        </div>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className="inline-flex min-h-11 items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm transition hover:bg-muted"
        >
          <Plus className="h-3.5 w-3.5" /> 新建假说
        </button>
      </div>

      {loading ? (
        <div className="flex h-20 items-center justify-center text-muted-foreground" role="status" aria-label="正在加载投资假说">
          <Loader2 className="h-4 w-4 animate-spin" />
        </div>
      ) : error ? (
        <ErrorRetryBanner message={error} onRetry={load} />
      ) : hypotheses.length === 0 ? (
        <div className="flex h-32 flex-col items-center justify-center gap-2 rounded-md border border-dashed text-sm text-muted-foreground">
          <Lightbulb className="h-6 w-6" />
          暂无假说。注册一个投资观点（如"谐波减速器国产替代率将从30%提升到60%"），持续追踪验证。
        </div>
      ) : (
        <div className="space-y-3">
          {hypotheses.map((h) => (
            <HypothesisCard key={h.hypothesis_id} hypothesis={h} />
          ))}
        </div>
      )}

      {showCreate && (
        <CreateForm
          chainId={chainId}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            load();
          }}
        />
      )}
    </div>
  );
}

function HypothesisCard({ hypothesis: h }: { hypothesis: ChainHypothesis }) {
  const style = STATUS_STYLE[h.status] ?? STATUS_STYLE.exploring;
  return (
    <div className="rounded-md border bg-card p-4">
      <div className="flex items-center gap-2">
        <Lightbulb className="h-4 w-4 text-amber-500" />
        <span className="font-medium">{h.title}</span>
        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${style.cls}`}>
          {style.label}
        </span>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{h.thesis}</p>
      {h.invalidation_notes && (
        <div className="mt-2 rounded bg-red-500/5 px-3 py-2 text-xs text-red-600 dark:text-red-400">
          否决条件: {h.invalidation_notes}
        </div>
      )}
      {h.run_cards.length > 0 && (
        <div className="mt-2 text-xs text-muted-foreground">
          {h.run_cards.length} 个关联回测
        </div>
      )}
      <div className="mt-2 text-xs text-muted-foreground">
        创建于 {h.created_at.slice(0, 10)}
      </div>
    </div>
  );
}

function CreateForm({
  chainId,
  onClose,
  onCreated,
}: {
  chainId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [title, setTitle] = useState("");
  const [thesis, setThesis] = useState("");
  const [invalidation, setInvalidation] = useState("");
  const [creating, setCreating] = useState(false);

  const submit = async () => {
    if (!title.trim() || !thesis.trim()) {
      toast.error("标题和论点不能为空");
      return;
    }
    setCreating(true);
    try {
      await api.createChainHypothesis(chainId, {
        title: title.trim(),
        thesis: thesis.trim(),
        invalidation_notes: invalidation.trim(),
      });
      toast.success("假说已创建");
      onCreated();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="rounded-md border bg-card p-5">
      <h4 className="mb-3 text-sm font-semibold">新建投资假说</h4>
      <div className="space-y-3">
        <div>
          <label htmlFor="hypothesis-title" className="mb-1 block text-xs font-medium text-muted-foreground">标题</label>
          <input
            id="hypothesis-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="如：谐波减速器国产替代加速"
            className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          />
        </div>
        <div>
          <label htmlFor="hypothesis-thesis" className="mb-1 block text-xs font-medium text-muted-foreground">论点（研究依据）</label>
          <textarea
            id="hypothesis-thesis"
            value={thesis}
            onChange={(e) => setThesis(e.target.value)}
            placeholder="如：绿的谐波产能扩张+下游机器人放量，预计国产化率从30%提升到60%"
            rows={3}
            className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          />
        </div>
        <div>
          <label htmlFor="hypothesis-invalidation" className="mb-1 block text-xs font-medium text-muted-foreground">否决条件（可选，可证伪）</label>
          <input
            id="hypothesis-invalidation"
            value={invalidation}
            onChange={(e) => setInvalidation(e.target.value)}
            placeholder="如：若日本厂商大幅降价15%+以上则逻辑失效"
            className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          />
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={submit}
            disabled={creating}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            {creating && <Loader2 className="h-4 w-4 animate-spin" />}
            创建
          </button>
          <button type="button" onClick={onClose} className="min-h-11 rounded-md border px-4 py-2 text-sm transition hover:bg-muted">
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
