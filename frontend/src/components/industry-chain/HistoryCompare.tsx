import { useEffect, useState } from "react";
import { GitCompare, Loader2 } from "lucide-react";
import { api, type ChainHistoryCompare, type ChainSnapshot } from "@/lib/api";

/** Compare two immutable research snapshots, including evidence-state drift. */
export function HistoryCompare({ chainId }: { chainId: string }) {
  const [snapshots, setSnapshots] = useState<ChainSnapshot[]>([]);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [result, setResult] = useState<ChainHistoryCompare | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.getChainHistory(chainId).then(({ snapshots: items }) => {
      setSnapshots(items);
      if (items.length >= 2) {
        setFrom(items[0].snapshot_id);
        setTo(items[items.length - 1].snapshot_id);
      }
    }).catch(() => setSnapshots([]));
  }, [chainId]);

  if (snapshots.length < 2) return null;

  const compare = async () => {
    if (!from || !to || from === to) return;
    setLoading(true);
    try {
      setResult(await api.compareChainHistory(chainId, from, to));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-md border bg-card p-5">
      <h3 className="mb-3 text-sm font-semibold text-muted-foreground">研究版本比较</h3>
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="起始研究版本" value={from} onChange={(event) => setFrom(event.target.value)} className="rounded border bg-background px-2 py-1.5 text-xs">
          {snapshots.map((snapshot) => <option key={snapshot.snapshot_id} value={snapshot.snapshot_id}>{snapshot.recorded_at}</option>)}
        </select>
        <span className="text-xs text-muted-foreground">至</span>
        <select aria-label="结束研究版本" value={to} onChange={(event) => setTo(event.target.value)} className="rounded border bg-background px-2 py-1.5 text-xs">
          {snapshots.map((snapshot) => <option key={snapshot.snapshot_id} value={snapshot.snapshot_id}>{snapshot.recorded_at}</option>)}
        </select>
        <button onClick={compare} disabled={loading || from === to} className="inline-flex items-center gap-1 rounded bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50">
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <GitCompare className="h-3 w-3" />} 比较
        </button>
      </div>
      {result && (
        <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
          <span>景气度变化：{formatChange(result.changes.prosperity_score)}</span>
          <span>板块评分变化：{formatChange(result.changes.sector_score)}</span>
          <span className="sm:col-span-2">证据状态变化：{Object.keys(result.changes.evidence_states.to).length} 个环节已记录</span>
        </div>
      )}
    </div>
  );
}

function formatChange(value: number | null) {
  if (value == null) return "证据不足";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}`;
}
