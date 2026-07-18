import { useTranslation } from 'react-i18next';
import { WifiOff, RefreshCw } from "lucide-react";
import type { SSEStatus } from "@/hooks/useSSE";
import { useOnlineStatus } from "@/hooks/useOnlineStatus";

interface Props {
  status: SSEStatus;
  retryAttempt?: number;
}

export function ConnectionBanner({ status, retryAttempt }: Props) {
  const { t } = useTranslation();
  const isOnline = useOnlineStatus();

  if (!isOnline) {
    return (
      <div className="flex items-center gap-2 px-4 py-2 text-xs bg-danger/15 text-danger border-b border-danger/30" role="status" aria-live="polite">
        <WifiOff className="h-3.5 w-3.5" aria-hidden="true" />
        <span>{t('connection.offline')}</span>
      </div>
    );
  }

  if (status === "connected" || status === "disconnected") return null;

  return (
    <div className="flex items-center gap-2 px-4 py-2 text-xs bg-warning/15 text-warning border-b border-warning/30" role="status" aria-live="polite">
      {status === "reconnecting" ? (
        <>
          <RefreshCw className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <span>{t('connection.reconnecting', { attempt: retryAttempt || 1 })}</span>
        </>
      ) : (
        <>
          <WifiOff className="h-3.5 w-3.5" aria-hidden="true" />
          <span>{t('connection.disconnected')}</span>
        </>
      )}
    </div>
  );
}
