import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Eye, Loader2 } from "lucide-react";
import { api, type ChainSwarmDetail, type SwarmTaskSummary } from "@/lib/api";
import { ErrorRetryBanner } from "@/components/common/ErrorRetryBanner";

interface Props {
  chainId: string;
  runId: string;
}

const GRADE_STYLE: Record<string, { cls: string; label: string }> = {
  A: { cls: "bg-green-500/15 text-green-600 dark:text-green-400", label: "A 优秀" },
  B: { cls: "bg-blue-500/15 text-blue-600 dark:text-blue-400", label: "B 良好" },
  C: { cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400", label: "C 合格" },
  D: { cls: "bg-red-500/15 text-red-600 dark:text-red-400", label: "D 不合格" },
  F: { cls: "bg-red-500/20 text-red-600 dark:text-red-400", label: "F 失败" },
};

const AGENT_LABELS: Record<string, string> = {
  chain_mapper: "链路拆解",
  chokepoint_scorer: "卡脖子评分",
  red_team_challenger: "红队挑战",
  dashboard_director: "综合裁决",
};

/** Swarm analysis insight panel: agent task progress, quality grades (A-F),
 * cross-validation summary (contradictions/consensus/blind spots). */
export function SwarmInsight({ chainId, runId }: Props) {
  const [detail, setDetail] = useState<ChainSwarmDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    setError(null);
    api
      .getChainSwarmDetail(chainId)
      .then(setDetail)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "加载分析详情失败"))
      .finally(() => setLoading(false));
  }, [chainId, runId, retryKey]);

  if (!runId) return null;
  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-md border bg-card p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载分析详情…
      </div>
    );
  }
  if (error) return <ErrorRetryBanner message={error} onRetry={() => setRetryKey((key) => key + 1)} />;
  if (!detail) return null;

  const gradeMap: Record<string, string> = {};
  for (const q of detail.quality) {
    if (q.grade && q.task_id) {
      gradeMap[q.task_id] = q.grade;
    }
  }

  const xval = detail.cross_validation[0];

  return (
    <div className="rounded-md border bg-card">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-5 py-3 text-sm font-medium transition hover:bg-muted/50"
      >
        <div className="flex items-center gap-2">
          <Eye className="h-4 w-4 text-muted-foreground" />
          分析洞察（门控质量 · 交叉验证 · Token 消耗）
        </div>
        <span className="text-xs text-muted-foreground">
          {expanded ? "收起" : "展开"}
        </span>
      </button>

      {expanded && (
        <div className="space-y-4 border-t px-5 py-4">
          {/* Agent task pipeline */}
          <div>
            <h4 className="mb-2 text-xs font-semibold text-muted-foreground">Agent 流水线</h4>
            <div className="space-y-1.5">
              {detail.tasks.map((t) => (
                <TaskRow key={t.task_id} task={t} grade={gradeMap[t.task_id]} />
              ))}
            </div>
          </div>

          {/* Cross-validation */}
          {xval && (
            <div>
              <h4 className="mb-2 text-xs font-semibold text-muted-foreground">交叉验证</h4>
              <div className="flex flex-wrap gap-3">
                <StatCard
                  icon={<AlertTriangle className="h-4 w-4 text-red-500" />}
                  label="矛盾点"
                  value={xval.contradictions_count}
                  sub={xval.high_severity > 0 ? `${xval.high_severity} 高严重度` : undefined}
                />
                <StatCard
                  icon={<CheckCircle2 className="h-4 w-4 text-green-500" />}
                  label="共识"
                  value={xval.consensus_count}
                />
                <StatCard
                  icon={<Eye className="h-4 w-4 text-amber-500" />}
                  label="盲区"
                  value={xval.blind_spots_count}
                />
              </div>
            </div>
          )}

          {/* Token usage */}
          <div className="flex gap-4 text-xs text-muted-foreground">
            <span>输入 tokens: {(detail.total_tokens.input ?? 0).toLocaleString()}</span>
            <span>输出 tokens: {(detail.total_tokens.output ?? 0).toLocaleString()}</span>
            <span>报告长度: {detail.final_report_length.toLocaleString()} 字符</span>
          </div>
        </div>
      )}
    </div>
  );
}

function TaskRow({ task, grade }: { task: SwarmTaskSummary; grade?: string }) {
  const label = AGENT_LABELS[task.agent_id] || task.agent_role || task.agent_id;
  const isDone = task.status === "completed";
  const isRunning = task.status === "running";

  return (
    <div className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
      {isRunning ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
      ) : isDone ? (
        <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
      ) : (
        <div className="h-3.5 w-3.5 rounded-full border" />
      )}
      <span className="flex-1 font-medium">{label}</span>
      {grade && (
        <span
          className={`rounded px-1.5 py-0.5 text-xs font-medium ${GRADE_STYLE[grade]?.cls ?? "bg-muted text-muted-foreground"}`}
          title={`门控质量评级: ${GRADE_STYLE[grade]?.label ?? grade}`}
        >
          {grade}
        </span>
      )}
      {task.summary_preview && (
        <span className="max-w-[200px] truncate text-xs text-muted-foreground" title={task.summary_preview}>
          {task.summary_preview.slice(0, 60)}…
        </span>
      )}
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  sub?: string;
}) {
  return (
    <div className="flex items-center gap-2 rounded-md border px-3 py-2">
      {icon}
      <div>
        <div className="text-sm font-semibold">{value}</div>
        <div className="text-xs text-muted-foreground">{label}</div>
        {sub && <div className="text-xs text-red-500">{sub}</div>}
      </div>
    </div>
  );
}
