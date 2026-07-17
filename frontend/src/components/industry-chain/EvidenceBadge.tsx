import { type ReactNode } from "react";

const TAG_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  P0: { bg: "bg-green-500/15", text: "text-green-600 dark:text-green-400", label: "P0 公司披露" },
  P1: { bg: "bg-blue-500/15", text: "text-blue-600 dark:text-blue-400", label: "P1 券商研报" },
  "P2 估算": { bg: "bg-amber-500/15", text: "text-amber-600 dark:text-amber-400", label: "P2 估算" },
  "预测": { bg: "bg-purple-500/15", text: "text-purple-600 dark:text-purple-400", label: "预测" },
  "来源不明": { bg: "bg-red-500/15", text: "text-red-600 dark:text-red-400", label: "来源不明" },
  P3: { bg: "bg-gray-500/15", text: "text-gray-500", label: "P3 媒体" },
};

const TAG_REGEX = /\[(P0|P1|P2 估算|P3|预测|来源不明)\]/g;

/** Render text with inline P-tag evidence badges. Segments without tags render
 * as plain text; tagged segments get a small colored badge after the text. */
export function EvidenceText({ text }: { text: string }) {
  if (!text || !TAG_REGEX.test(text)) {
    return <span>{text}</span>;
  }

  TAG_REGEX.lastIndex = 0;
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = TAG_REGEX.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(<span key={lastIndex}>{text.slice(lastIndex, match.index)}</span>);
    }
    const tag = match[1];
    const style = TAG_STYLES[tag] ?? TAG_STYLES["来源不明"];
    parts.push(
      <span
        key={match.index}
        className={`ml-0.5 inline-flex rounded px-1 py-px text-[10px] font-medium ${style.bg} ${style.text}`}
        title={style.label}
      >
        {tag}
      </span>,
    );
    lastIndex = TAG_REGEX.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push(<span key={lastIndex}>{text.slice(lastIndex)}</span>);
  }

  return <span>{parts}</span>;
}

/** Standalone evidence-tier badge for use in tables and lists. */
export function EvidenceTierBadge({ tier }: { tier: string }) {
  const style = TAG_STYLES[tier] ?? TAG_STYLES["来源不明"];
  return (
    <span className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium ${style.bg} ${style.text}`}>
      {tier}
    </span>
  );
}
