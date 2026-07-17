import { renderHook } from "@testing-library/react";
import { useAbortController } from "../useAbortController";

describe("useAbortController", () => {
  it("returns a getSignal function", () => {
    const { result } = renderHook(() => useAbortController());
    expect(typeof result.current.getSignal).toBe("function");
  });

  it("returns an AbortSignal from getSignal", () => {
    const { result } = renderHook(() => useAbortController());
    const signal = result.current.getSignal();
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal.aborted).toBe(false);
  });

  it("aborts previous signal when getSignal is called again", () => {
    const { result } = renderHook(() => useAbortController());
    const signal1 = result.current.getSignal();
    result.current.getSignal();
    expect(signal1.aborted).toBe(true);
  });

  it("aborts signal on unmount", () => {
    const { result, unmount } = renderHook(() => useAbortController());
    const signal = result.current.getSignal();
    unmount();
    expect(signal.aborted).toBe(true);
  });
});
