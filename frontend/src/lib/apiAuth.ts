const STORAGE_KEY = "vibe_trading_api_auth_key";
const OBFUSCATED_KEY = "vt_ak";

function encode(v: string): string { return btoa(unescape(encodeURIComponent(v))); }
function decode(v: string): string { try { return decodeURIComponent(escape(atob(v))); } catch { return ""; } }

export function getApiAuthKey(): string {
  const obfuscated = window.sessionStorage.getItem(OBFUSCATED_KEY);
  if (obfuscated) return decode(obfuscated);
  const legacy = window.localStorage.getItem(STORAGE_KEY);
  if (legacy) {
    window.sessionStorage.setItem(OBFUSCATED_KEY, encode(legacy));
    window.localStorage.removeItem(STORAGE_KEY);
    return legacy;
  }
  return "";
}

export function setApiAuthKey(value: string): void {
  const trimmed = value.trim();
  window.localStorage.removeItem(STORAGE_KEY);
  if (trimmed) {
    window.sessionStorage.setItem(OBFUSCATED_KEY, encode(trimmed));
  } else {
    window.sessionStorage.removeItem(OBFUSCATED_KEY);
  }
}

export function authHeaders(): Record<string, string> {
  const key = getApiAuthKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

export function authQuerySuffix(): string {
  const key = getApiAuthKey();
  return key ? `api_key=${encodeURIComponent(key)}` : "";
}

export function withAuthQuery(url: string): string {
  const suffix = authQuerySuffix();
  if (!suffix) return url;
  return `${url}${url.includes("?") ? "&" : "?"}${suffix}`;
}
