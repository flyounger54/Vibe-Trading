import { useEffect, useRef, useState } from "react";
import { Loader2, CheckCircle2, XCircle } from "lucide-react";
import { api, type ChainStatus } from "@/lib/api";

interface Props {
  chainId: string;
  /** Called once the run reaches a terminal state (ready/error). */
  onDone: () => void;
}

const STAGE_LABELS = ["环节拆解", "卡脖子评分", "红队挑战", "综合裁决"];

/** Polls the chain analysis status while a swarm run is in flight. The swarm
 * is a long task (tens of minutes), so this polls every few seconds rather
 * than streaming, mirroring the project's long-job UX. */
export function AnalysisProgress({ chainId, onDone }: Props) {
  const [status, setStatus] = useState<ChainStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const doneRef = useRef(false);

  useEffect(() => {
    doneRef.current = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const s = await api.getChainStatus(chainId);
        setStatus(s);
        if (s.status === "ready" || s.status === "error") {
          if (!doneRef.current) {
            doneRef.current = true;
            onDone();
          }
          return;
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "状态查询失败");
      }
      timer = setTimeout(poll, 5000);
    };
    poll();
    return () => clearTimeout(timer);
  }, [chainId, onDone]);

  const completed = status?.completed_count ?? 0;
  const total = status?.task_count ?? STAGE_LABELS.length;

  return (
    <div className="rounded-md border bg-card p-5">
      <div className="mb-4 flex items-center gap-2">
        {status?.status === "error" ? (
          <XCircle className="h-5 w-5 text-red-500" />
        ) : status?.status === "ready" ? (
          <CheckCircle2 className="h-5 w-5 text-green-500" />
        ) : (
          <Loader2 className="h-5 w-5 animate-spin text-primary" />
        )}
        <span className="text-sm font-medium">
          {status?.status === "error"
            ? "分析失败"
            : status?.status === "ready"
              ? "分析完成"
              : "多Agent 分析中…（约需数十分钟，可离开页面，稍后回来查看）"}
        </span>
      </div>

      <div className="flex gap-2">
        {STAGE_LABELS.map((label, i) => {
          const reached = i < completed;
          const active = i === completed && status?.status === "analyzing";
          return (
            <div key={label} className="flex-1">
              <div
                className={`h-1.5 rounded-full ${
                  reached ? "bg-primary" : active ? "bg-primary/40" : "bg-muted"
                }`}
              />
              <div className="mt-1.5 flex items-center gap-1 text-xs text-muted-foreground">
                {active && <Loader2 className="h-3 w-3 animate-spin" />}
                {label}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-3 text-xs text-muted-foreground">
        {completed}/{total} 阶段完成
        {status?.run_id && <span className="ml-2 font-mono">· {status.run_id}</span>}
      </div>
      {error && <div className="mt-2 text-xs text-red-500">{error}</div>}
    </div>
  );
}
