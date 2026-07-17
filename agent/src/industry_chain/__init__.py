"""Industry-chain research dashboard: structured, persistent veneer over the
``supply_chain_research_team`` swarm.

This package owns only persistence and templating for the dashboard. The
analysis itself is delegated to the existing swarm engine (see
``src/swarm/presets/industry_chain_dashboard.yaml``) — this layer organizes the
result into a ``chain -> segments -> tickers`` tree, persists it, and tracks
chain-level prosperity over time.
"""

from __future__ import annotations

from src.industry_chain.store import (
    Chain,
    ChainOverview,
    IndustryChainStore,
    Segment,
    Ticker,
)

__all__ = [
    "Chain",
    "ChainOverview",
    "IndustryChainStore",
    "Segment",
    "Ticker",
]
