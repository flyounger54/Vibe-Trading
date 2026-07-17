import { renderHook, act } from "@testing-library/react";
import { useDraft } from "../useDraft";

beforeEach(() => {
  sessionStorage.clear();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useDraft", () => {
  it("initializes with empty string when no stored value", () => {
    const { result } = renderHook(() => useDraft("test-key"));
    expect(result.current[0]).toBe("");
  });

  it("initializes with stored value from sessionStorage", () => {
    sessionStorage.setItem("test-key", "saved draft");
    const { result } = renderHook(() => useDraft("test-key"));
    expect(result.current[0]).toBe("saved draft");
  });

  it("updates value immediately on setDraft", () => {
    const { result } = renderHook(() => useDraft("test-key"));
    act(() => result.current[1]("hello"));
    expect(result.current[0]).toBe("hello");
  });

  it("debounce-saves to sessionStorage", () => {
    const { result } = renderHook(() => useDraft("test-key"));
    act(() => result.current[1]("draft text"));
    expect(sessionStorage.getItem("test-key")).toBeNull();

    act(() => vi.advanceTimersByTime(600));
    expect(sessionStorage.getItem("test-key")).toBe("draft text");
  });

  it("clearDraft resets value and removes from storage", () => {
    sessionStorage.setItem("test-key", "old draft");
    const { result } = renderHook(() => useDraft("test-key"));
    act(() => result.current[2]());
    expect(result.current[0]).toBe("");
    expect(sessionStorage.getItem("test-key")).toBeNull();
  });

  it("updates value when key changes", () => {
    sessionStorage.setItem("key-a", "value-a");
    sessionStorage.setItem("key-b", "value-b");
    const { result, rerender } = renderHook(({ key }) => useDraft(key), {
      initialProps: { key: "key-a" },
    });
    expect(result.current[0]).toBe("value-a");

    rerender({ key: "key-b" });
    expect(result.current[0]).toBe("value-b");
  });
});
