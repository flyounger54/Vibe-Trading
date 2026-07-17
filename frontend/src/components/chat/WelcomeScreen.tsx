import { useTranslation } from "react-i18next";
import { useState as useLocalState } from "react";
import { Bot, BookOpen, TrendingUp, Globe, Sparkles, Users, UserCircle2, NotebookPen, Landmark, ChevronRight } from "lucide-react";

interface Example {
  title: string;
  desc: string;
  prompt: string;
}

interface Category {
  label: string;
  icon: React.ReactNode;
  color: string;
  cardColor: string;
  examples: Example[];
}

const CATEGORIES: Category[] = [
  {
    label: "Multi-Market Backtest",
    icon: <TrendingUp className="h-4 w-4" />,
    color: "text-red-400",
    cardColor: "border-red-500/30 hover:border-red-500/60 hover:bg-red-500/5",
    examples: [
      {
        title: "Cross-Market Portfolio",
        desc: "A-shares + crypto + US equities with risk-parity optimizer",
        prompt: "Backtest a risk-parity portfolio of 000001.SZ, BTC-USDT, and AAPL for full-year 2024, compare against equal-weight baseline",
      },
      {
        title: "BTC 5-Min MACD Strategy",
        desc: "Minute-level crypto backtest with real-time OKX data",
        prompt: "Backtest BTC-USDT 5-minute MACD strategy, fast=12 slow=26 signal=9, last 30 days",
      },
      {
        title: "US Tech Max Diversification",
        desc: "Portfolio optimizer across FAANG+ via yfinance",
        prompt: "Backtest AAPL, MSFT, GOOGL, AMZN, NVDA with max_diversification portfolio optimizer, full-year 2024",
      },
    ],
  },
  {
    label: "Research & Analysis",
    icon: <Sparkles className="h-4 w-4" />,
    color: "text-amber-400",
    cardColor: "border-amber-500/30 hover:border-amber-500/60 hover:bg-amber-500/5",
    examples: [
      {
        title: "Multi-Factor Alpha Model",
        desc: "IC-weighted factor synthesis across 300 stocks",
        prompt: "Build a multi-factor alpha model using momentum, reversal, volatility, and turnover on CSI 300 constituents with IC-weighted factor synthesis, backtest 2023-2024",
      },
      {
        title: "Options Greeks Analysis",
        desc: "Black-Scholes pricing with Delta/Gamma/Theta/Vega",
        prompt: "Calculate option Greeks using Black-Scholes: spot=100, strike=105, risk-free rate=3%, vol=25%, expiry=90 days, analyze Delta/Gamma/Theta/Vega",
      },
    ],
  },
  {
    label: "Swarm Teams",
    icon: <Users className="h-4 w-4" />,
    color: "text-violet-400",
    cardColor: "border-violet-500/30 hover:border-violet-500/60 hover:bg-violet-500/5",
    examples: [
      {
        title: "Investment Committee Review",
        desc: "Multi-agent debate: long vs short, risk review, PM decision",
        prompt: "[Swarm Team Mode] Use the investment_committee preset to evaluate whether to go long or short on NVDA given current market conditions",
      },
      {
        title: "Quant Strategy Desk",
        desc: "Screening → factor research → backtest → risk audit pipeline",
        prompt: "[Swarm Team Mode] Use the quant_strategy_desk preset to find and backtest the best momentum strategy on CSI 300 constituents",
      },
    ],
  },
  {
    label: "Document & Web Research",
    icon: <Globe className="h-4 w-4" />,
    color: "text-blue-400",
    cardColor: "border-blue-500/30 hover:border-blue-500/60 hover:bg-blue-500/5",
    examples: [
      {
        title: "Analyze an Earnings Report PDF",
        desc: "Upload a PDF and ask questions about the financials",
        prompt: "Summarize the key financial metrics, risks, and outlook from the uploaded earnings report",
      },
      {
        title: "Web Research: Macro Outlook",
        desc: "Read live web sources for macro analysis",
        prompt: "Read the latest Fed meeting minutes and summarize the key takeaways for equity and crypto markets",
      },
    ],
  },
  {
    label: "Trade Journal",
    icon: <NotebookPen className="h-4 w-4" />,
    color: "text-orange-400",
    cardColor: "border-orange-500/30 hover:border-orange-500/60 hover:bg-orange-500/5",
    examples: [
      {
        title: "Analyze My Broker Export",
        desc: "Parse 同花顺/东财/富途/generic CSV — holding days, win rate, PnL ratio, hourly distribution",
        prompt: "Analyze the trade journal I just uploaded — full profile with holding stats, win rate, top symbols, and hourly distribution",
      },
      {
        title: "Diagnose My Behavior Biases",
        desc: "Disposition effect, overtrading, chasing momentum, anchoring — severity + numeric evidence",
        prompt: "Run the 4 behavior diagnostics on my trade journal (disposition, overtrading, chasing, anchoring) and tell me which bias hurts my PnL most",
      },
    ],
  },
  {
    label: "Trading Connectors",
    icon: <Landmark className="h-4 w-4" />,
    color: "text-cyan-400",
    cardColor: "border-cyan-500/30 hover:border-cyan-500/60 hover:bg-cyan-500/5",
    examples: [
      {
        title: "Check Selected Connector",
        desc: "List connector profiles and verify the selected one",
        prompt: "List my trading connector profiles, show which one is selected, then check that selected connector. If it is not ready, tell me exactly what setup step is missing. Do not place or modify orders.",
      },
      {
        title: "Analyze Connector Portfolio",
        desc: "Read account summary and positions from the selected connector",
        prompt: "Use the selected trading connector profile to summarize my account, positions, concentration, cash, and portfolio risk. Do not place or modify orders.",
      },
      {
        title: "Quote & Trend",
        desc: "Fetch a quote plus recent daily bars through the selected connector",
        prompt: "Use the selected trading connector to fetch an AAPL quote and 30 daily bars, then summarize the current quote versus the recent trend. Keep it read-only.",
      },
    ],
  },
  {
    label: "Shadow Account",
    icon: <UserCircle2 className="h-4 w-4" />,
    color: "text-emerald-400",
    cardColor: "border-emerald-500/30 hover:border-emerald-500/60 hover:bg-emerald-500/5",
    examples: [
      {
        title: "Train My Shadow from Journal",
        desc: "Extract your strategy rules from a broker CSV and persist a Shadow profile",
        prompt: "Train my shadow account from the trading journal I just uploaded — show the extracted rules and confirm they look like my behavior",
      },
      {
        title: "How Much Am I Leaving on the Table?",
        desc: "Backtest your shadow strategy and attribute delta vs. your actual PnL",
        prompt: "Run a shadow backtest for the last 90 days on the US market and break down where my PnL diverged from the shadow (rule violations, early exits, missed signals)",
      },
      {
        title: "Generate Shadow Report",
        desc: "8-section HTML/PDF — equity curve, per-market Sharpe, attribution waterfall",
        prompt: "Render the shadow report and give me the URL — lead with the you-vs-shadow delta",
      },
    ],
  },
];

const CAPABILITY_CHIPS = [
  "Finance Skills Library",
  "Swarm Agent Teams",
  "Auto-Discovered Tools",
  "3 Markets: A-Share · Crypto · HK/US",
  "Trading Connector Profiles",
  "Minute to Daily Timeframes",
  "4 Portfolio Optimizers",
  "15+ Risk Metrics",
  "Options & Derivatives",
  "PDF & Web Research",
  "Factor Analysis & ML",
  "Trade Journal Analyzer",
  "Shadow Account Backtest",
  "Persistent Memory",
  "Session Search",
];

interface Props {
  onExample: (s: string) => void;
  onOpenSwarmGuide?: () => void;
}

export function WelcomeScreen({ onExample, onOpenSwarmGuide }: Props) {
  const { t } = useTranslation();
  const [chipsOpen, setChipsOpen] = useLocalState(false);
  return (
    <div className="flex flex-col space-y-3 py-2">
      {/* Compact header with collapsible capability chips */}
      <div className="flex items-center gap-3">
        <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-primary/80 to-info/80 flex items-center justify-center shadow shrink-0">
          <Bot className="h-5 w-5 text-white" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold tracking-tight leading-tight">{t('welcome.title')}</h2>
          <p className="text-xs text-muted-foreground leading-snug">
            Describe a trading strategy to get started.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setChipsOpen((o) => !o)}
          className="shrink-0 flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
        >
          <ChevronRight className={`h-3 w-3 transition-transform ${chipsOpen ? "rotate-90" : ""}`} />
          {CAPABILITY_CHIPS.length} capabilities
        </button>
      </div>

      {chipsOpen && (
        <div className="flex flex-wrap gap-1.5">
          {CAPABILITY_CHIPS.map((chip) => (
            <span
              key={chip}
              className="px-2 py-0.5 text-[10px] rounded-full border border-border/40 text-muted-foreground/70"
            >
              {chip}
            </span>
          ))}
        </div>
      )}

      {/* Example categories — dense grid layout */}
      <div className="space-y-3">
        <p className="text-xs text-muted-foreground">{t('welcome.tryExample')}</p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-3">
          {CATEGORIES.map((cat) => {
            const isWide = cat.examples.length === 3;
            return (
              <div
                key={cat.label}
                className={`flex flex-col ${isWide ? "sm:col-span-2" : ""}`}
              >
                <div className={`flex items-center gap-1.5 text-xs font-medium mb-1.5 ${cat.color}`}>
                  {cat.icon}
                  <span>{cat.label}</span>
                  {cat.label === "Swarm Teams" && onOpenSwarmGuide && (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); onOpenSwarmGuide(); }}
                      className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground hover:text-violet-500 transition-colors"
                    >
                      <BookOpen className="h-3 w-3" />
                      View all 33 presets →
                    </button>
                  )}
                </div>
                <div className={`grid flex-1 gap-1.5 ${isWide ? "grid-cols-1 sm:grid-cols-3" : "grid-cols-1"}`}>
                  {cat.examples.map((ex) => (
                    <button
                      key={ex.title}
                      onClick={() => onExample(ex.prompt)}
                      className={`flex flex-col items-start w-full text-left px-3 py-2 rounded-lg border transition-colors ${cat.cardColor}`}
                    >
                      <span className="text-sm font-medium text-foreground leading-snug">
                        {ex.title}
                      </span>
                      <span className="block text-[11px] text-muted-foreground mt-0.5 leading-snug">
                        {ex.desc}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
