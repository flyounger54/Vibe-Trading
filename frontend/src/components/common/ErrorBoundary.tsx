import i18n from "@/i18n";
import { Component, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

interface Props { children: ReactNode; fallback?: ReactNode; }
interface State { hasError: boolean; error?: Error; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? (
        <div className="flex flex-wrap items-center gap-2 p-4 rounded-lg border border-destructive/30 bg-destructive/5 text-sm text-destructive" role="alert">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span className="flex-1">{this.state.error?.message || i18n.t("errorBoundary.somethingWrong")}</span>
          <button
            type="button"
            onClick={() => this.setState({ hasError: false, error: undefined })}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-md bg-destructive/10 px-3 py-2 font-medium"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            {i18n.t("layout.retry")}
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
