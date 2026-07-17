import type { AgentMessage } from "@/types/agent";
import type { GoalSnapshot, LiveAction, LiveHalted, LiveStatus, MandateProposal } from "@/lib/api";

/* ---------- Message grouping ---------- */
export type MsgGroup =
  | { kind: "single"; msg: AgentMessage }
  | { kind: "timeline"; msgs: AgentMessage[] };

export function groupMessages(msgs: AgentMessage[]): MsgGroup[] {
  const out: MsgGroup[] = [];
  let buf: AgentMessage[] = [];
  const flush = () => { if (buf.length) { out.push({ kind: "timeline", msgs: [...buf] }); buf = []; } };
  for (const m of msgs) {
    if (["thinking", "tool_call", "tool_result", "compact"].includes(m.type)) {
      buf.push(m);
    } else {
      flush();
      out.push({ kind: "single", msg: m });
    }
  }
  flush();
  return out;
}

/* ---------- Connector runtime types ---------- */
export interface ProposalItem {
  kind: "proposal";
  timestamp: number;
  proposal: MandateProposal;
}
export interface LiveActionItem {
  kind: "live_action";
  timestamp: number;
  action: LiveAction;
}
export type LiveItem = ProposalItem | LiveActionItem;

export type TimelineRow =
  | { sort: number; render: "group"; group: MsgGroup; key: string }
  | { sort: number; render: "live"; item: LiveItem; key: string };

export function normalizeBrokerScope(broker: string | null | undefined): string | null {
  const normalized = broker?.trim().toLowerCase();
  return normalized || null;
}

export function isGlobalLiveHalt(halt: LiveHalted | null): boolean {
  return halt != null && normalizeBrokerScope(halt.broker) == null;
}

export function haltScopeStillActive(halt: LiveHalted, status: LiveStatus): boolean {
  const broker = normalizeBrokerScope(halt.broker);
  if (!broker) return status.global_halted;
  return status.global_halted || status.brokers.some((item) => (
    normalizeBrokerScope(item.auth.broker) === broker && item.halted
  ));
}

export function liveActionLabel(action: LiveAction): string {
  return action.kind.replace(/_/g, " ");
}

/* ---------- Goal helpers ---------- */
export function isCriterionStatusMet(status: string): boolean {
  return !["", "pending", "open", "unsatisfied"].includes(status.toLowerCase());
}

export function criterionEvidenceCount(snapshot: GoalSnapshot, criterionId: string): number {
  return snapshot.evidence.filter((item) => item.criterion_id === criterionId).length;
}

export function criterionCovered(snapshot: GoalSnapshot, criterion: GoalSnapshot["criteria"][number]): boolean {
  return isCriterionStatusMet(criterion.status) || criterionEvidenceCount(snapshot, criterion.criterion_id) > 0;
}

export function getGoalProgress(snapshot: GoalSnapshot | null): {
  met: number;
  total: number;
  label: string;
  metLabel: string;
  evidenceTotal: number;
} {
  const total = snapshot?.criteria.length ?? 0;
  const met = snapshot?.criteria.filter((item) => criterionCovered(snapshot!, item)).length ?? 0;
  const evidenceTotal = snapshot?.evidence_count ?? 0;
  return {
    met,
    total,
    label: total > 0 ? `${met}/${total}` : "",
    metLabel: total > 0 ? `${met}/${total} met` : "",
    evidenceTotal,
  };
}

export function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

export function isTerminalGoalStatus(status: string): boolean {
  return ["complete", "cancelled", "blocked", "superseded", "usage_limited"].includes(status);
}

export function criterionIndexLabel(index: number): string {
  return String(index + 1);
}

export function latestGoalEvidence(snapshot: GoalSnapshot) {
  return [...snapshot.evidence]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .slice(0, 2);
}

export function goalKickoffPrompt(objective: string): string {
  return [
    "Start working on this research goal now.",
    "Keep it research-only, use available tools when evidence is needed, add concrete evidence to the goal ledger, and keep going until the goal is complete, blocked, waiting for user input, or budget-limited.",
    "",
    `Goal: ${objective}`,
  ].join("\n");
}

export function goalContinuePrompt(snapshot: GoalSnapshot): string {
  const openCriteria = snapshot.criteria
    .filter((item) => item.required && !criterionCovered(snapshot, item))
    .map((item) => `- ${item.text}`)
    .join("\n");
  return [
    "Continue the active research goal.",
    "Use real available tools as needed, add evidence to the goal ledger, and only stop when the goal is complete, blocked, waiting for user input, or budget-limited.",
    "",
    `Goal: ${snapshot.goal.objective}`,
    openCriteria ? `Open criteria:\n${openCriteria}` : "All criteria appear covered; audit the ledger and update the goal status if completion is justified.",
  ].join("\n");
}

export const LIVE_STATUS_POLL_INTERVAL_MS = 15_000;
export const CONNECTOR_CHECK_PROMPT =
  "List my trading connector profiles, show which one is selected, then check that selected connector. If it is not ready, tell me exactly what setup step is missing. Do not place or modify orders.";
export const CONNECTOR_PORTFOLIO_PROMPT =
  "Use the selected trading connector profile to summarize my account, positions, concentration, cash, and portfolio risk. Do not place or modify orders.";
