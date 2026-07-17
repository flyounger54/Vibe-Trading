import { AlertTriangle, RotateCcw } from "lucide-react";

interface Props {
  message: string;
  onRetry: () => void;
}

export function ErrorRetryBanner({ message, onRetry }: Props) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-danger/30 bg-danger/5 px-4 py-3">
      <AlertTriangle className="h-4 w-4 text-danger shrink-0" />
      <span className="flex-1 text-sm text-danger">{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="flex items-center gap-1.5 rounded-md bg-danger/10 px-3 py-1.5 text-xs font-medium text-danger hover:bg-danger/20 transition-colors"
      >
        <RotateCcw className="h-3 w-3" />
        Retry
      </button>
    </div>
  );
}
