import { AlertTriangle, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";

interface Props {
  message: string;
  onRetry: () => void;
}

export function ErrorRetryBanner({ message, onRetry }: Props) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-danger/30 bg-danger/5 px-4 py-3" role="alert" aria-live="assertive">
      <AlertTriangle className="h-4 w-4 text-danger shrink-0" aria-hidden="true" />
      <span className="flex-1 text-sm text-danger">{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="flex min-h-11 items-center gap-1.5 rounded-md bg-danger/10 px-3 py-2 text-xs font-medium text-danger hover:bg-danger/20 transition-colors"
      >
        <RotateCcw className="h-3 w-3" aria-hidden="true" />
        {t("layout.retry")}
      </button>
    </div>
  );
}
