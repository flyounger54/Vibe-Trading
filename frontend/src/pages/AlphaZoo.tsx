import { useLocation, useParams } from "react-router-dom";
import { BrowseView } from "./alpha-zoo/BrowseView";
import { DetailView } from "./alpha-zoo/DetailView";
import { BenchView } from "./alpha-zoo/BenchView";
import { CompareView } from "./alpha-zoo/CompareView";

export function AlphaZoo() {
  const params = useParams<{ alphaId?: string }>();
  const { pathname } = useLocation();

  if (pathname === "/alpha-zoo/bench") {
    return <BenchView />;
  }
  if (pathname === "/alpha-zoo/compare") {
    return <CompareView />;
  }
  if (params.alphaId) {
    return <DetailView alphaId={params.alphaId} />;
  }
  return <BrowseView />;
}
