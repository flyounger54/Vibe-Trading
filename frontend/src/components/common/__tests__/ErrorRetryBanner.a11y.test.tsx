import { render } from "@testing-library/react";
import { axe } from "vitest-axe";
import { ErrorRetryBanner } from "../ErrorRetryBanner";

describe("ErrorRetryBanner accessibility", () => {
  it("has no a11y violations", async () => {
    const { container } = render(
      <ErrorRetryBanner message="Something went wrong" onRetry={() => {}} />,
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
