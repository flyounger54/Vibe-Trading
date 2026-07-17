import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, AlertTriangle, Info, XCircle } from "lucide-react";

export function ContextTip({ tipKey }: { tipKey: string }) {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = useState(() => {
    try { return sessionStorage.getItem(`ml-tip-${tipKey}`) === "1"; } catch { return false; }
  });

  if (dismissed) return null;

  const dismiss = () => {
    setDismissed(true);
    try { sessionStorage.setItem(`ml-tip-${tipKey}`, "1"); } catch {}
  };

  return (
    <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-info/5 border border-info/20 text-sm">
      <Info className="h-4 w-4 text-info shrink-0 mt-0.5" />
      <p className="flex-1 text-muted-foreground">{t(`mlTraining.tips.${tipKey}`)}</p>
      <button onClick={dismiss} className="text-muted-foreground hover:text-foreground text-xs shrink-0">
        <XCircle className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ===========================================================================
// Shared components
// ===========================================================================

export function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex-1 min-w-0">
      <label className="text-xs text-muted-foreground block mb-1">{label}</label>
      {children}
    </div>
  );
}

export function LoadingState() {
  return (
    <div className="p-8 flex items-center justify-center text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin mr-2" />
      Loading…
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="border border-destructive/30 rounded-xl p-4 bg-destructive/5 flex items-center gap-2 text-sm">
      <AlertTriangle className="h-4 w-4 text-destructive shrink-0" />
      <span>{message}</span>
    </div>
  );
}
