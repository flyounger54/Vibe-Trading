import { resolveGeneratedOperation } from "@/generated/api-client";

describe("generated API client", () => {
  it("builds encoded v1 paths and query parameters from the OpenAPI operation", () => {
    expect(resolveGeneratedOperation(
      "v1_get_correlation_matrix_api_v1_correlation_get",
      { query: { codes: "AAPL,沪深300", days: 90, method: "spearman" } },
    )).toEqual({
      method: "GET",
      path: "/api/v1/correlation?codes=AAPL%2C%E6%B2%AA%E6%B7%B1300&days=90&method=spearman",
      body: undefined,
    });
  });
});
