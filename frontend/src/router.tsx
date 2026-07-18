import { Suspense, lazy, type ComponentType } from "react";
import { createBrowserRouter } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { NotFoundPage, RouteErrorPage } from "@/components/common/RouteState";
import { useTranslation } from "react-i18next";

const Home = lazy(() => import("@/pages/Home").then((m) => ({ default: m.Home })));
const Agent = lazy(() => import("@/pages/Agent").then((m) => ({ default: m.Agent })));
const RunDetail = lazy(() =>
  import("@/pages/RunDetail").then((m) => ({ default: m.RunDetail })),
);
const Compare = lazy(() =>
  import("@/pages/Compare").then((m) => ({ default: m.Compare })),
);
const Settings = lazy(() =>
  import("@/pages/Settings").then((m) => ({ default: m.Settings })),
);
const Runtime = lazy(() =>
  import("@/pages/Runtime").then((m) => ({ default: m.Runtime })),
);
const Reports = lazy(() =>
  import("@/pages/Reports").then((m) => ({ default: m.Reports })),
);
const Correlation = lazy(() =>
  import("@/pages/Correlation").then((m) => ({ default: m.Correlation })),
);
const AlphaZoo = lazy(() =>
  import("@/pages/AlphaZoo").then((m) => ({ default: m.AlphaZoo })),
);
const StrategyZoo = lazy(() =>
  import("@/pages/StrategyZoo").then((m) => ({ default: m.StrategyZoo })),
);
const MLTraining = lazy(() =>
  import("@/pages/MLTraining").then((m) => ({ default: m.MLTraining })),
);
const IndustryChain = lazy(() =>
  import("@/pages/IndustryChain").then((m) => ({ default: m.IndustryChain })),
);

function PageLoader() {
  const { t } = useTranslation();
  return (
    <div className="flex h-[60vh] items-center justify-center text-muted-foreground" role="status" aria-live="polite">
      {t("routeState.loading")}
    </div>
  );
}

function wrap(Component: ComponentType) {
  return (
    <Suspense fallback={<PageLoader />}>
      <Component />
    </Suspense>
  );
}

export const prefetchRoute: Record<string, () => void> = {
  "/": () => void import("@/pages/Home"),
  "/agent": () => void import("@/pages/Agent"),
  "/runtime": () => void import("@/pages/Runtime"),
  "/reports": () => void import("@/pages/Reports"),
  "/settings": () => void import("@/pages/Settings"),
  "/correlation": () => void import("@/pages/Correlation"),
  "/alpha-zoo": () => void import("@/pages/AlphaZoo"),
  "/strategy-zoo": () => void import("@/pages/StrategyZoo"),
  "/ml-training": () => void import("@/pages/MLTraining"),
  "/industry-chain": () => void import("@/pages/IndustryChain"),
};

export const router = createBrowserRouter([
  {
    element: <Layout />,
    errorElement: <RouteErrorPage />,
    children: [
      { path: "/", element: wrap(Home) },
      { path: "/agent", element: wrap(Agent) },
      { path: "/runtime", element: wrap(Runtime) },
      { path: "/reports", element: wrap(Reports) },
      { path: "/settings", element: wrap(Settings) },
      { path: "/runs/:runId", element: wrap(RunDetail) },
      { path: "/compare", element: wrap(Compare) },
      { path: "/correlation", element: wrap(Correlation) },
      { path: "/alpha-zoo", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/bench", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/compare", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/:alphaId", element: wrap(AlphaZoo) },
      { path: "/strategy-zoo", element: wrap(StrategyZoo) },
      { path: "/strategy-zoo/:strategyId", element: wrap(StrategyZoo) },
      { path: "/ml-training", element: wrap(MLTraining) },
      { path: "/ml-training/:tab", element: wrap(MLTraining) },
      { path: "/industry-chain", element: wrap(IndustryChain) },
      { path: "/industry-chain/:chainId", element: wrap(IndustryChain) },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
