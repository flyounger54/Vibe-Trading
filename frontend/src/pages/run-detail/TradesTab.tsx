import i18n from "@/i18n";
import { cn } from "@/lib/utils";
import type { RunData } from "@/lib/api";

const EXIT_REASON_STYLES: Record<string, string> = {
  stop_loss: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  trailing_stop: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400",
  take_profit: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  timeout: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
  reduce: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
};
const EXIT_REASON_LABELS: Record<string, string> = {
  stop_loss: "止损",
  trailing_stop: "移动止损",
  take_profit: "止盈",
  timeout: "超时",
  reduce: "减仓",
  signal: "信号",
};

function ExitReasonBadge({ reason }: { reason: string }) {
  const style = EXIT_REASON_STYLES[reason];
  const label = EXIT_REASON_LABELS[reason] || reason;
  if (style) {
    return <span className={cn("inline-block px-1.5 py-0.5 rounded text-xs font-medium", style)}>{label}</span>;
  }
  return <span className="text-muted-foreground text-xs">{label}</span>;
}

export function TradesTab({ run }: { run: RunData }) {
  const trades = run.trade_log || [];
  if (trades.length === 0) return <div className="p-8 text-muted-foreground text-sm">{i18n.t("runDetail.noTrades")}</div>;
  return (
    <div className="p-4">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-muted-foreground">
            <th className="py-2 pr-4">{i18n.t("runDetail.time")}</th>
            <th className="py-2 pr-4">{i18n.t("runDetail.code2")}</th>
            <th className="py-2 pr-4">{i18n.t("runDetail.side")}</th>
            <th className="py-2 pr-4">{i18n.t("runDetail.price")}</th>
            <th className="py-2 pr-4">{i18n.t("runDetail.qty")}</th>
            <th className="py-2">{i18n.t("runDetail.reason")}</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((tr, i) => (
            <tr key={i} className="border-b last:border-0 hover:bg-muted/20">
              <td className="py-2 pr-4 font-mono text-xs">{tr.time || tr.timestamp}</td>
              <td className="py-2 pr-4">{tr.code}</td>
              <td className={cn("py-2 pr-4 font-medium", tr.side === "BUY" ? "text-success" : "text-danger")}>{tr.side}</td>
              <td className="py-2 pr-4 tabular-nums">{tr.price}</td>
              <td className="py-2 pr-4 tabular-nums">{tr.qty}</td>
              <td className="py-2"><ExitReasonBadge reason={tr.reason} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

