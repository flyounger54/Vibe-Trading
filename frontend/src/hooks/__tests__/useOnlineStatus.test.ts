import { renderHook, act } from "@testing-library/react";
import { useOnlineStatus } from "../useOnlineStatus";

describe("useOnlineStatus", () => {
  it("returns true when navigator.onLine is true", () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    const { result } = renderHook(() => useOnlineStatus());
    expect(result.current).toBe(true);
  });

  it("returns false when navigator.onLine is false", () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const { result } = renderHook(() => useOnlineStatus());
    expect(result.current).toBe(false);
  });

  it("updates when online/offline events fire", () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    const { result } = renderHook(() => useOnlineStatus());
    expect(result.current).toBe(true);

    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    act(() => { window.dispatchEvent(new Event("offline")); });
    expect(result.current).toBe(false);

    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    act(() => { window.dispatchEvent(new Event("online")); });
    expect(result.current).toBe(true);
  });
});
