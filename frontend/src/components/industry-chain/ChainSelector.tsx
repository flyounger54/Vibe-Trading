import { useState } from "react";
import { Plus, X, GitBranch, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, type ChainSummary, type ChainTemplate } from "@/lib/api";

interface Props {
  chains: ChainSummary[];
  templates: ChainTemplate[];
  activeChainId: string | null;
  onSelect: (chainId: string) => void;
  onCreated: (chainId: string) => void;
  onDeleted: (chainId: string) => void;
}

const STAGE_COLOR: Record<string, string> = {
  Discovery: "#22c55e",
  Validation: "#3b82f6",
  Mainstream: "#f59e0b",
  Exhaustion: "#ef4444",
};

/** Left rail: list of chains as cards + a create modal supporting both
 * template-seeded and custom chains. */
export function ChainSelector({
  chains,
  templates,
  activeChainId,
  onSelect,
  onCreated,
  onDeleted,
}: Props) {
  const [showCreate, setShowCreate] = useState(false);

  const handleDelete = async (e: React.MouseEvent, chainId: string) => {
    e.stopPropagation();
    if (!confirm("确认删除这条产业链？")) return;
    try {
      await api.deleteChain(chainId);
      toast.success("已删除");
      onDeleted(chainId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  return (
    <div className="space-y-3">
      <button
        onClick={() => setShowCreate(true)}
        className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-dashed px-4 py-2.5 text-sm font-medium text-muted-foreground transition hover:bg-muted"
      >
        <Plus className="h-4 w-4" /> 新建产业链
      </button>

      {chains.length === 0 && (
        <p className="px-1 text-xs text-muted-foreground">还没有产业链，从模板或自定义创建一条。</p>
      )}

      {chains.map((c) => (
        <div
          key={c.chain_id}
          onClick={() => onSelect(c.chain_id)}
          className={`group cursor-pointer rounded-md border p-3 transition hover:border-primary/50 ${
            activeChainId === c.chain_id ? "border-primary bg-primary/5" : "bg-card"
          }`}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <GitBranch className="h-4 w-4 text-muted-foreground" />
              <span className="font-medium">{c.name}</span>
            </div>
            <button
              onClick={(e) => handleDelete(e, c.chain_id)}
              className="opacity-0 transition group-hover:opacity-100"
            >
              <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-red-500" />
            </button>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
              {c.segment_count} 环节
            </span>
            {c.lifecycle_stage && (
              <span
                className="rounded px-1.5 py-0.5 text-xs font-medium text-white"
                style={{ background: STAGE_COLOR[c.lifecycle_stage] ?? "#64748b" }}
              >
                {c.lifecycle_stage}
              </span>
            )}
            <StatusDot status={c.status} />
          </div>
        </div>
      ))}

      {showCreate && (
        <CreateModal
          templates={templates}
          onClose={() => setShowCreate(false)}
          onCreated={(id) => {
            setShowCreate(false);
            onCreated(id);
          }}
        />
      )}
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    draft: { label: "草稿", cls: "text-muted-foreground" },
    analyzing: { label: "分析中", cls: "text-amber-500" },
    ready: { label: "已就绪", cls: "text-green-500" },
    error: { label: "失败", cls: "text-red-500" },
  };
  const s = map[status] ?? map.draft;
  return <span className={`text-xs ${s.cls}`}>· {s.label}</span>;
}

function CreateModal({
  templates,
  onClose,
  onCreated,
}: {
  templates: ChainTemplate[];
  onClose: () => void;
  onCreated: (chainId: string) => void;
}) {
  const [mode, setMode] = useState<"template" | "custom">("template");
  const [customName, setCustomName] = useState("");
  const [customSegments, setCustomSegments] = useState("");
  const [creating, setCreating] = useState(false);

  const createTemplate = async (key: string) => {
    setCreating(true);
    try {
      const res = await api.createChain({ template_key: key, market: "A" });
      toast.success("已创建");
      onCreated(res.chain_id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败");
    } finally {
      setCreating(false);
    }
  };

  const createCustom = async () => {
    if (!customName.trim()) {
      toast.error("请输入产业链名称");
      return;
    }
    setCreating(true);
    try {
      const segs = customSegments
        .split(/[,，\n]/)
        .map((s) => s.trim())
        .filter(Boolean);
      const res = await api.createChain({ name: customName.trim(), segment_names: segs, market: "A" });
      toast.success("已创建");
      onCreated(res.chain_id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-lg border bg-card p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-semibold">新建产业链</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>

        <div className="mb-4 flex gap-1 rounded-md border p-1">
          <button
            onClick={() => setMode("template")}
            className={`flex-1 rounded px-3 py-1.5 text-sm transition ${
              mode === "template" ? "bg-primary text-primary-foreground" : "text-muted-foreground"
            }`}
          >
            从模板
          </button>
          <button
            onClick={() => setMode("custom")}
            className={`flex-1 rounded px-3 py-1.5 text-sm transition ${
              mode === "custom" ? "bg-primary text-primary-foreground" : "text-muted-foreground"
            }`}
          >
            自定义
          </button>
        </div>

        {mode === "template" ? (
          <div className="grid grid-cols-2 gap-3">
            {templates.map((t) => (
              <button
                key={t.key}
                disabled={creating}
                onClick={() => createTemplate(t.key)}
                className="rounded-md border p-3 text-left transition hover:border-primary/50 hover:bg-muted disabled:opacity-50"
              >
                <div className="font-medium">{t.name}</div>
                <div className="mt-1 text-xs text-muted-foreground">{t.segment_count} 环节</div>
                <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">{t.description}</div>
              </button>
            ))}
          </div>
        ) : (
          <div className="space-y-3">
            <div>
              <label htmlFor="industry-chain-name" className="mb-1 block text-xs font-medium text-muted-foreground">产业链名称</label>
              <input
                id="industry-chain-name"
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
                placeholder="如：低空经济"
                className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
              />
            </div>
            <div>
              <label htmlFor="industry-chain-segments" className="mb-1 block text-xs font-medium text-muted-foreground">
                环节（逗号或换行分隔，可留空由 AI 自动发现）
              </label>
              <textarea
                id="industry-chain-segments"
                value={customSegments}
                onChange={(e) => setCustomSegments(e.target.value)}
                placeholder="如：eVTOL整机, 航空发动机, 飞控系统, 复合材料"
                rows={3}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
              />
            </div>
            <button
              disabled={creating}
              onClick={createCustom}
              className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
            >
              {creating && <Loader2 className="h-4 w-4 animate-spin" />}
              创建
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
