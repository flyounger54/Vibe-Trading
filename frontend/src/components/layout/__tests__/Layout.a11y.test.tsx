import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "vitest-axe";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ConnectionBanner } from "../ConnectionBanner";
import { Layout } from "../Layout";
import { api } from "@/lib/api";

vi.mock("@/router", () => ({ prefetchRoute: {} }));
vi.mock("@/lib/api", () => ({
  api: {
    listSessions: vi.fn(),
    deleteSession: vi.fn(),
    renameSession: vi.fn(),
  },
}));

function renderLayout(path = "/reports") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/reports" element={<h1>Reports content</h1>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Layout accessibility", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(api.listSessions).mockResolvedValue([]);
  });

  it("ConnectionBanner — reconnecting state has no a11y violations", async () => {
    const { container } = render(
      <MemoryRouter>
        <ConnectionBanner status="reconnecting" retryAttempt={2} />
      </MemoryRouter>,
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it("ConnectionBanner — offline state has no a11y violations", async () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const { container } = render(
      <MemoryRouter>
        <ConnectionBanner status="disconnected" />
      </MemoryRouter>,
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it("renders the application shell with a labelled current route", async () => {
    const { container } = renderLayout();

    expect(await screen.findByRole("heading", { name: "Reports content" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Reports" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Open navigation" })).toHaveAttribute("aria-expanded", "false");

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it("exposes and dismisses the mobile navigation drawer", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
      matches: query === "(max-width: 767px)",
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    renderLayout();

    const openButton = screen.getByRole("button", { name: "Open navigation" });
    expect(screen.queryByRole("navigation", { name: "Primary navigation" })).not.toBeInTheDocument();
    await user.click(openButton);
    expect(openButton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Close navigation" })).toHaveLength(2);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Close navigation" })[1]).toHaveFocus());

    await user.click(screen.getAllByRole("button", { name: "Close navigation" })[0]);
    expect(openButton).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("navigation", { name: "Primary navigation" })).not.toBeInTheDocument();
  });

  it("shows a recoverable session-list failure instead of swallowing it", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listSessions)
      .mockRejectedValueOnce(new Error("backend unavailable"))
      .mockResolvedValueOnce([]);
    renderLayout();

    expect(await screen.findByRole("alert")).toHaveTextContent("Sessions are unavailable.");
    await user.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(api.listSessions).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByText("No sessions yet")).toBeInTheDocument();
  });
});
