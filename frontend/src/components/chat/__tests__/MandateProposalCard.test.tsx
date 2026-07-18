import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MandateProposal } from "@/lib/api";
import { MandateProposalCard } from "../MandateProposalCard";

const apiMock = vi.hoisted(() => ({
  commitMandate: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, api: apiMock };
});

const proposal: MandateProposal = {
  proposal_id: "mp_11111111111111111111111111111111",
  session_id: "session-1",
  account: { broker: "robinhood", type: "cash", funded_by: "user" },
  profiles: [
    {
      ordinal: 1,
      label: "稳健",
      universe: ["AAPL", "MSFT"],
      max_order_usd: 250,
      daily_trade_cap: 2,
      max_daily_loss_usd: 50,
      max_price_deviation_bps: 25,
      max_quote_age_seconds: 15,
      max_clock_drift_seconds: 3,
      leverage: "none",
      instruments: ["equity"],
    },
  ],
};

describe("MandateProposalCard", () => {
  beforeEach(() => {
    apiMock.commitMandate.mockReset();
    apiMock.commitMandate.mockResolvedValue({ mandate_id: "mandate-1" });
  });

  it("shows every Node 12B execution control before consent", () => {
    render(<MandateProposalCard proposal={proposal} onAdjust={vi.fn()} />);

    expect(screen.getByText("Max daily loss")).toBeInTheDocument();
    expect(screen.getByText("$50")).toBeInTheDocument();
    expect(screen.getByText("Price deviation")).toBeInTheDocument();
    expect(screen.getByText("25 bps")).toBeInTheDocument();
    expect(screen.getByText("Max quote age")).toBeInTheDocument();
    expect(screen.getByText("15s")).toBeInTheDocument();
    expect(screen.getByText("Max clock drift")).toBeInTheDocument();
    expect(screen.getByText("3s")).toBeInTheDocument();
  });

  it("commits the exact rendered option through the privileged API", async () => {
    const user = userEvent.setup();
    render(<MandateProposalCard proposal={proposal} onAdjust={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Commit “稳健”" }));

    await waitFor(() => {
      expect(apiMock.commitMandate).toHaveBeenCalledWith({
        broker: "robinhood",
        proposal_id: proposal.proposal_id,
        selected_ordinal: 1,
        adjustments: null,
        consent_ack: true,
        session_id: "session-1",
      });
    });
  });

  it("keeps the daily-loss boundary visible after the commit event", () => {
    render(
      <MandateProposalCard
        proposal={proposal}
        committed={{ selected_ordinal: 1, max_daily_loss_usd: 50 }}
        onAdjust={vi.fn()}
      />,
    );

    expect(screen.getByText(/loss ≤\$50\/day/)).toBeInTheDocument();
  });
});
