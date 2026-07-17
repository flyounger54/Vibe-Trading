import { render } from "@testing-library/react";
import { axe } from "vitest-axe";
import { MemoryRouter } from "react-router-dom";
import { ConnectionBanner } from "../ConnectionBanner";

describe("Layout accessibility", () => {
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
});
