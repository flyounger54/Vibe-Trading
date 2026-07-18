import { AlertTriangle, Home, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useRouteError } from "react-router-dom";

function StateShell({ title, message, retry }: { title: string; message: string; retry?: () => void }) {
  const { t } = useTranslation();
  return (
    <section className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center gap-4 p-6 text-center" role="alert">
      <AlertTriangle className="h-10 w-10 text-warning" aria-hidden="true" />
      <div>
        <h1 className="text-2xl font-semibold">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{message}</p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        {retry ? (
          <button type="button" onClick={retry} className="inline-flex min-h-11 items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">
            <RotateCcw className="h-4 w-4" aria-hidden="true" />
            {t("layout.retry")}
          </button>
        ) : null}
        <Link to="/" className="inline-flex min-h-11 items-center gap-2 rounded-md border px-4 py-2 text-sm font-medium hover:bg-muted">
          <Home className="h-4 w-4" aria-hidden="true" />
          {t("layout.home")}
        </Link>
      </div>
    </section>
  );
}

export function RouteErrorPage() {
  const { t } = useTranslation();
  const error = useRouteError();
  const message = error instanceof Error ? error.message : t("routeState.errorBody");
  return <StateShell title={t("routeState.errorTitle")} message={message} retry={() => window.location.reload()} />;
}

export function NotFoundPage() {
  const { t } = useTranslation();
  return <StateShell title={t("routeState.notFoundTitle")} message={t("routeState.notFoundBody")} />;
}
