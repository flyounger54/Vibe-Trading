export interface ZooCard {
  id: string;
  title: string;
  description: string;
  approxCount: number;
  accent: string;
}

export const ZOO_CARDS: ZooCard[] = [
  {
    id: "qlib158",
    title: "Qlib 158",
    description:
      "Microsoft Qlib's full 158-feature library covering momentum, volatility, volume and rolling statistical signals.",
    approxCount: 154,
    accent: "from-sky-500/20 to-sky-500/5",
  },
  {
    id: "alpha101",
    title: "Kakushadze 101 Formulaic Alphas",
    description:
      "The 101 formulaic alphas from Kakushadze (2015); short-horizon cross-sectional signals.",
    approxCount: 101,
    accent: "from-emerald-500/20 to-emerald-500/5",
  },
  {
    id: "gtja191",
    title: "GTJA 191",
    description:
      "Guotai Junan Securities' 191 alphas; technical and microstructure signals tuned to China A-share markets.",
    approxCount: 191,
    accent: "from-amber-500/20 to-amber-500/5",
  },
  {
    id: "academic",
    title: "Academic Anomalies",
    description:
      "Curated long-horizon anomalies from the academic literature (value, momentum, quality, low-vol, etc.).",
    approxCount: 6,
    accent: "from-violet-500/20 to-violet-500/5",
  },
];

export const UNIVERSE_OPTIONS = [
  { value: "csi300", label: "CSI 300 (China A)" },
  { value: "sp500", label: "S&P 500 (US)" },
  { value: "btc-usdt", label: "BTC-USDT (Crypto)" },
];

export function fmtNum(v: unknown, digits = 3): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

export function metaString(meta: Record<string, unknown>, key: string): string {
  const v = meta[key];
  if (v === undefined || v === null || v === "") return "—";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}
