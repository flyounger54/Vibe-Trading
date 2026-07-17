import { getApiAuthKey, setApiAuthKey, authHeaders } from "../apiAuth";

describe("apiAuth", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  describe("getApiAuthKey", () => {
    it("returns empty string when nothing stored", () => {
      expect(getApiAuthKey()).toBe("");
    });
    it("returns stored key from sessionStorage", () => {
      setApiAuthKey("my-secret");
      expect(getApiAuthKey()).toBe("my-secret");
    });
    it("migrates legacy localStorage key to sessionStorage", () => {
      localStorage.setItem("vibe_trading_api_auth_key", "legacy-key");
      expect(getApiAuthKey()).toBe("legacy-key");
      expect(localStorage.getItem("vibe_trading_api_auth_key")).toBeNull();
      expect(getApiAuthKey()).toBe("legacy-key");
    });
  });

  describe("setApiAuthKey", () => {
    it("stores trimmed value", () => {
      setApiAuthKey("  abc-123  ");
      expect(getApiAuthKey()).toBe("abc-123");
    });
    it("removes key when value is empty/whitespace", () => {
      setApiAuthKey("abc");
      setApiAuthKey("   ");
      expect(getApiAuthKey()).toBe("");
    });
    it("removes key when value is empty string", () => {
      setApiAuthKey("abc");
      setApiAuthKey("");
      expect(getApiAuthKey()).toBe("");
    });
    it("clears legacy localStorage on set", () => {
      localStorage.setItem("vibe_trading_api_auth_key", "old");
      setApiAuthKey("new");
      expect(localStorage.getItem("vibe_trading_api_auth_key")).toBeNull();
    });
  });

  describe("authHeaders", () => {
    it("returns empty object when no key set", () => {
      expect(authHeaders()).toEqual({});
    });
    it("returns Bearer header when key exists", () => {
      setApiAuthKey("token-xyz");
      expect(authHeaders()).toEqual({ Authorization: "Bearer token-xyz" });
    });
  });

});
