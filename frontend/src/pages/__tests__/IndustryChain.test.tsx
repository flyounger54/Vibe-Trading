import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { IndustryChain } from "../IndustryChain";
import type { Chain, ChainSummary, ChainTemplate } from "@/lib/api";

const apiMock = vi.hoisted(() => ({
  listChains: vi.fn(),
  listChainTemplates: vi.fn(),
  getChain: vi.fn(),
  createChain: vi.fn(),
  deleteChain: vi.fn(),
  analyzeChain: vi.fn(),
  cancelChainAnalysis: vi.fn(),
  retryChainAnalysis: vi.fn(),
  setChainSchedule: vi.fn(),
  exportChain: vi.fn(),
  compareChains: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("@/components/industry-chain/ChainOverview", () => ({
  ChainOverview: ({ chain }: { chain: Chain }) => <div>overview {chain.name}</div>,
}));
vi.mock("@/components/industry-chain/SegmentDetail", () => ({
  SegmentDetail: () => <div>segment</div>,
}));
vi.mock("@/components/industry-chain/RedTeamLog", () => ({ RedTeamLog: () => <div>red team</div> }));
vi.mock("@/components/industry-chain/AnalysisProgress", () => ({ AnalysisProgress: () => <div>analysis progress</div> }));
vi.mock("@/components/industry-chain/SwarmInsight", () => ({ SwarmInsight: () => <div>swarm insight</div> }));
vi.mock("@/components/industry-chain/HypothesisPanel", () => ({ HypothesisPanel: () => <div>hypotheses</div> }));

const template: ChainTemplate = {
  key: "ai", name: "AI算力", name_en: "AI", description: "template", segment_count: 1,
};

function chain(status = "draft"): Chain {
  return {
    chain_id: "chain-1", name: "AI算力", name_en: "AI", description: "", market: "A", status,
    template_key: "", swarm_run_id: "", refresh_job_id: "", refresh_schedule: "", created_at: "2026-07-18T00:00:00Z",
    updated_at: "2026-07-18T00:00:00Z", as_of: "", research_version: 0, row_version: 1,
    last_error: status === "error" ? "结构化证据缺失" : "",
    overview: { structure_summary: "", lifecycle_stage: "", prosperity_score: null, sector_score: null, core_targets: [], evidence_ids: [], evidence_state: "missing" },
    segments: [], nodes: [], edges: [], evidence: [], conflicts: [],
  };
}

function summary(id: string, name: string): ChainSummary {
  return {
    chain_id: id, name, name_en: "", description: "", market: "A", status: "draft", template_key: "",
    segment_count: 1, lifecycle_stage: "", prosperity_score: null, refresh_schedule: "", updated_at: "2026-07-18T00:00:00Z",
  };
}

function renderPage(path = "/industry-chain") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/industry-chain" element={<IndustryChain />} />
        <Route path="/industry-chain/:chainId" element={<IndustryChain />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("IndustryChain workflow", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    apiMock.listChainTemplates.mockResolvedValue({ templates: [template] });
    apiMock.listChains.mockResolvedValue({ chains: [] });
    apiMock.getChain.mockResolvedValue(chain());
    apiMock.createChain.mockResolvedValue({ status: "created", chain_id: "chain-1", chain: chain() });
    apiMock.analyzeChain.mockResolvedValue({ status: "queued", chain_id: "chain-1", run_id: "", job_id: "job-1" });
    apiMock.retryChainAnalysis.mockResolvedValue({ status: "queued", chain_id: "chain-1", run_id: "", job_id: "job-2" });
    apiMock.exportChain.mockResolvedValue({ chain_id: "chain-1", filename: "report.md", markdown: "# report" });
    apiMock.setChainSchedule.mockResolvedValue({ status: "updated", chain_id: "chain-1", refresh_schedule: "weekly" });
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("covers create, queued analysis failure, retry, and export without hiding the failure", async () => {
    let reads = 0;
    apiMock.getChain.mockImplementation(async () => {
      reads += 1;
      return reads >= 2 ? chain("error") : chain();
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /新建产业链/ }));
    fireEvent.click(screen.getByRole("button", { name: "自定义" }));
    fireEvent.change(screen.getByLabelText("产业链名称"), { target: { value: "AI算力" } });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await screen.findByText("overview AI算力");

    fireEvent.click(screen.getByRole("button", { name: /一键分析/ }));
    await waitFor(() => expect(apiMock.analyzeChain).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("alert")).toHaveTextContent("结构化证据缺失");

    fireEvent.click(screen.getByRole("button", { name: /重试/ }));
    await waitFor(() => expect(apiMock.retryChainAnalysis).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "导出" }));
    await waitFor(() => expect(apiMock.exportChain).toHaveBeenCalledWith("chain-1"));
  });

  it("covers the cross-chain comparison entry flow", async () => {
    apiMock.listChains.mockResolvedValue({ chains: [summary("a", "AI"), summary("b", "半导体")] });
    apiMock.compareChains.mockResolvedValue({ chains: [], shared_tickers: [] });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /跨链对比/ }));
    fireEvent.click(screen.getByRole("button", { name: "AI" }));
    fireEvent.click(screen.getByRole("button", { name: "半导体" }));
    fireEvent.click(screen.getByRole("button", { name: "对比" }));
    await waitFor(() => expect(apiMock.compareChains).toHaveBeenCalledWith("a,b"));
  });
});
