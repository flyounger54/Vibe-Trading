"""Crash-safe store for industry-chain research dashboards.

Each chain lives in its own directory under the user runtime root
(``~/.vibe-trading/industry_chains/{chain_id}/``), holding two files:

* ``chain.json``   — the full current state (metadata + overview + segments).
* ``history.json`` — append-only analysis snapshots for prosperity time-series.

Both use the same atomic write pattern as
``src.scheduled_research.store`` (temp file in the same dir -> fsync ->
``os.replace`` -> fsync parent dir) so a SIGKILL at any point leaves either the
old complete file or the new one, never a partial write.

The analysis itself is delegated to the ``supply_chain_research_team`` swarm;
this module only persists the structured result. Segment scoring fields mirror
the 6-dimension chokepoint framework so the dashboard can render them directly.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config.paths import get_runtime_root

logger = logging.getLogger(__name__)

_CHAIN_FILENAME = "chain.json"
_HISTORY_FILENAME = "history.json"
_SCHEMA_VERSION = 1

# Chain lifecycle status values.
STATUS_DRAFT = "draft"
STATUS_ANALYZING = "analyzing"
STATUS_READY = "ready"
STATUS_ERROR = "error"
_VALID_STATUS = (STATUS_DRAFT, STATUS_ANALYZING, STATUS_READY, STATUS_ERROR)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Return a short unique identifier for a chain or segment."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Schema (dataclasses as DTOs)
# ---------------------------------------------------------------------------


@dataclass
class Ticker:
    """A single core target within a segment.

    Attributes:
        code: Symbol such as ``"300124.SZ"`` (A-share) or ``"NVDA"`` (US).
        name: Display name.
        market: ``"A"`` | ``"US"`` | ``"HK"``.
        score: Chokepoint score 0-100, or ``None`` before analysis.
        tier: ``"Core"`` | ``"Build"`` | ``"Watch"`` | ``"Skip"`` from the
            research director, or ``""`` before analysis.
        classification: Three-way role — ``"Controller"`` | ``"Integrator"`` |
            ``"Beneficiary"`` — or ``""``.
        confidence: Evidence confidence label (``"high"``/``"medium"``/...).
        key_products: Short free-text product description.
        red_team_note: One-line red-team verdict, or ``""``.
    """

    code: str
    name: str = ""
    market: str = "A"
    score: Optional[float] = None
    tier: str = ""
    classification: str = ""
    confidence: str = ""
    key_products: str = ""
    red_team_note: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Ticker":
        """Build a Ticker from a plain dict, ignoring unknown keys."""
        return cls(
            code=str(data.get("code", "")),
            name=str(data.get("name", "")),
            market=str(data.get("market", "A")),
            score=_as_float(data.get("score")),
            tier=str(data.get("tier", "")),
            classification=str(data.get("classification", "")),
            confidence=str(data.get("confidence", "")),
            key_products=str(data.get("key_products", "")),
            red_team_note=str(data.get("red_team_note", "")),
        )


@dataclass
class Segment:
    """One link (环节) in the industry chain.

    Mirrors Simon's per-segment template plus the chokepoint scoring fields.
    """

    name: str
    segment_id: str = field(default_factory=_new_id)
    name_en: str = ""
    order: int = 0
    positioning: str = ""  # 上游 / 中游 / 下游
    value_weight: str = ""  # share of total chain value, free text
    localization_rate: str = ""  # 国产化进展
    international_competition: str = ""
    domestic_competition: str = ""
    barrier_type: str = ""  # 科技壁垒 / 产能壁垒 / 认证壁垒
    barrier_description: str = ""
    # 6-dimension chokepoint score (segment level). Keys match
    # supply_chain_tool dimensions; empty until analysis fills it.
    chokepoint_score: Dict[str, float] = field(default_factory=dict)
    chokepoint_total: Optional[float] = None
    status: str = "empty"  # empty | partial | complete
    tickers: List[Ticker] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Segment":
        """Build a Segment from a plain dict, ignoring unknown keys."""
        return cls(
            name=str(data.get("name", "")),
            segment_id=str(data.get("segment_id") or _new_id()),
            name_en=str(data.get("name_en", "")),
            order=int(data.get("order", 0) or 0),
            positioning=str(data.get("positioning", "")),
            value_weight=str(data.get("value_weight", "")),
            localization_rate=str(data.get("localization_rate", "")),
            international_competition=str(data.get("international_competition", "")),
            domestic_competition=str(data.get("domestic_competition", "")),
            barrier_type=str(data.get("barrier_type", "")),
            barrier_description=str(data.get("barrier_description", "")),
            chokepoint_score=dict(data.get("chokepoint_score") or {}),
            chokepoint_total=_as_float(data.get("chokepoint_total")),
            status=str(data.get("status", "empty")),
            tickers=[Ticker.from_dict(t) for t in (data.get("tickers") or [])],
        )


@dataclass
class ChainOverview:
    """Chain-level rollup shown on the Overview tab.

    Chain-level prosperity (theme lifecycle) is distinct from segment-level
    chokepoint scores: it answers "how hot is this whole chain", not "which
    link is the bottleneck".
    """

    structure_summary: str = ""
    lifecycle_stage: str = ""  # Discovery | Validation | Mainstream | Exhaustion
    prosperity_score: Optional[float] = None  # 0-100 chain-level
    sector_score: Optional[float] = None
    core_targets: List[Ticker] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChainOverview":
        """Build a ChainOverview from a plain dict."""
        return cls(
            structure_summary=str(data.get("structure_summary", "")),
            lifecycle_stage=str(data.get("lifecycle_stage", "")),
            prosperity_score=_as_float(data.get("prosperity_score")),
            sector_score=_as_float(data.get("sector_score")),
            core_targets=[Ticker.from_dict(t) for t in (data.get("core_targets") or [])],
        )


@dataclass
class Chain:
    """A full industry chain: metadata + overview + segments."""

    name: str
    chain_id: str = field(default_factory=_new_id)
    name_en: str = ""
    description: str = ""
    market: str = "A"
    status: str = STATUS_DRAFT
    template_key: str = ""  # which template seeded it, "" for custom
    swarm_run_id: str = ""  # last analysis run
    refresh_schedule: str = ""  # "" | "weekly" | "monthly"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    overview: ChainOverview = field(default_factory=ChainOverview)
    segments: List[Segment] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-ready dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Chain":
        """Build a Chain from a plain dict, ignoring unknown keys."""
        return cls(
            name=str(data.get("name", "")),
            chain_id=str(data.get("chain_id") or _new_id()),
            name_en=str(data.get("name_en", "")),
            description=str(data.get("description", "")),
            market=str(data.get("market", "A")),
            status=str(data.get("status", STATUS_DRAFT)),
            template_key=str(data.get("template_key", "")),
            swarm_run_id=str(data.get("swarm_run_id", "")),
            refresh_schedule=str(data.get("refresh_schedule", "")),
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or _now_iso()),
            overview=ChainOverview.from_dict(data.get("overview") or {}),
            segments=[Segment.from_dict(s) for s in (data.get("segments") or [])],
        )

    def summary(self) -> Dict[str, Any]:
        """Return a lightweight dict for list views (no segment internals)."""
        return {
            "chain_id": self.chain_id,
            "name": self.name,
            "name_en": self.name_en,
            "description": self.description,
            "market": self.market,
            "status": self.status,
            "template_key": self.template_key,
            "segment_count": len(self.segments),
            "lifecycle_stage": self.overview.lifecycle_stage,
            "prosperity_score": self.overview.prosperity_score,
            "refresh_schedule": self.refresh_schedule,
            "updated_at": self.updated_at,
        }


def _as_float(value: Any) -> Optional[float]:
    """Coerce a value to float, or None when missing/non-numeric."""
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class IndustryChainStore:
    """Durable, crash-safe persistence for industry-chain dashboards.

    The store owns only serialization and atomic I/O. Each chain is a directory
    under :attr:`root` containing ``chain.json`` and ``history.json``.

    Attributes:
        root: Directory holding one subdirectory per chain.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        """Initialize the store.

        Args:
            root: Explicit root directory. Defaults to
                ``~/.vibe-trading/industry_chains``.
        """
        self.root: Path = root if root is not None else get_runtime_root() / "industry_chains"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_chains(self) -> List[Chain]:
        """Load all chains, newest-updated first.

        Returns:
            All persisted chains. Empty when none exist. A single corrupt
            chain file is logged and skipped rather than aborting the listing.
        """
        if not self.root.exists():
            return []
        chains: List[Chain] = []
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            chain = self._load_chain_file(child / _CHAIN_FILENAME)
            if chain is not None:
                chains.append(chain)
        chains.sort(key=lambda c: c.updated_at, reverse=True)
        return chains

    def get_chain(self, chain_id: str) -> Optional[Chain]:
        """Return a chain by id, or ``None`` when it does not exist."""
        return self._load_chain_file(self._chain_dir(chain_id) / _CHAIN_FILENAME)

    def save_chain(self, chain: Chain) -> Chain:
        """Persist a chain (full object), refreshing ``updated_at``.

        Args:
            chain: The chain to store.

        Returns:
            The same chain instance with ``updated_at`` bumped.
        """
        chain.updated_at = _now_iso()
        target = self._chain_dir(chain.chain_id) / _CHAIN_FILENAME
        self._atomic_write_json(target, self._envelope(chain.to_dict()))
        return chain

    def delete_chain(self, chain_id: str) -> bool:
        """Delete a chain and all its files.

        Returns:
            ``True`` when a chain directory was removed, ``False`` otherwise.
        """
        chain_dir = self._chain_dir(chain_id)
        if not chain_dir.exists():
            return False
        for item in chain_dir.iterdir():
            try:
                item.unlink()
            except OSError:
                logger.warning("Failed to remove %s", item, exc_info=True)
        try:
            chain_dir.rmdir()
        except OSError:
            logger.warning("Failed to remove chain dir %s", chain_dir, exc_info=True)
            return False
        return True

    def append_history(self, chain_id: str, snapshot: Dict[str, Any]) -> None:
        """Append a timestamped analysis snapshot for prosperity time-series.

        Args:
            chain_id: Target chain id.
            snapshot: Arbitrary JSON-serializable summary (e.g. lifecycle stage,
                prosperity score, top segment scores) at analysis time.
        """
        history = self.load_history(chain_id)
        entry = {"recorded_at": _now_iso(), **snapshot}
        history.append(entry)
        target = self._chain_dir(chain_id) / _HISTORY_FILENAME
        self._atomic_write_json(target, {"schema_version": _SCHEMA_VERSION, "snapshots": history})

    def load_history(self, chain_id: str) -> List[Dict[str, Any]]:
        """Return the analysis-snapshot history for a chain (oldest first)."""
        path = self._chain_dir(chain_id) / _HISTORY_FILENAME
        if not path.exists():
            return []
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            snapshots = envelope.get("snapshots", [])
            return snapshots if isinstance(snapshots, list) else []
        except (OSError, ValueError) as exc:
            logger.warning("Failed to read history for %s: %s", chain_id, exc)
            return []

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _chain_dir(self, chain_id: str) -> Path:
        """Return the directory for a chain (not necessarily existing)."""
        return self.root / chain_id

    def _load_chain_file(self, path: Path) -> Optional[Chain]:
        """Load and parse a chain.json file, or None on missing/corrupt."""
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            payload = envelope.get("chain", envelope)
            return Chain.from_dict(payload)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Skipping corrupt chain file %s: %s", path, exc)
            return None

    @staticmethod
    def _envelope(chain_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Wrap a chain dict with a schema version for forward-compat."""
        return {"schema_version": _SCHEMA_VERSION, "chain": chain_dict}

    @staticmethod
    def _atomic_write_json(target: Path, payload: Dict[str, Any]) -> None:
        """Atomically write JSON: temp -> fsync -> replace -> fsync dir.

        Mirrors ``src.scheduled_research.store`` so a crash at any step leaves
        either the old complete file or the new one, never a partial write.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, target)
        IndustryChainStore._fsync_dir(target.parent)

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        """fsync a directory so a rename is durable. Best-effort on platforms
        that disallow opening a directory."""
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)
