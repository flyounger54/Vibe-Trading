"""Unified durable store for industry-chain research dashboards.

Current state, immutable history snapshots, and refresh schedules all live in
the shared SQLite :class:`~src.state.database.StateDatabase`.  The old
``industry_chains/<id>/{chain,history}.json`` layout is read once as a
compatibility import; it is never the authoritative write path.  This gives
industry research the same WAL durability, optimistic concurrency, and
restart semantics as sessions, swarm runs, and jobs.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config.paths import get_runtime_root
from src.security.boundaries import resolve_within_root
from src.state.database import ConcurrentUpdateError, StateDatabase, default_state_db_path

logger = logging.getLogger(__name__)

_CHAIN_FILENAME = "chain.json"
_HISTORY_FILENAME = "history.json"
_SCHEMA_VERSION = 2

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
    evidence_ids: List[str] = field(default_factory=list)
    evidence_state: str = "missing"

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
            evidence_ids=[str(item) for item in (data.get("evidence_ids") or [])],
            evidence_state=str(data.get("evidence_state", "missing")),
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
    evidence_ids: List[str] = field(default_factory=list)
    evidence_state: str = "missing"  # supported | stale | conflicting | missing

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
            evidence_ids=[str(item) for item in (data.get("evidence_ids") or [])],
            evidence_state=str(data.get("evidence_state", "missing")),
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
    evidence_ids: List[str] = field(default_factory=list)
    evidence_state: str = "missing"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChainOverview":
        """Build a ChainOverview from a plain dict."""
        return cls(
            structure_summary=str(data.get("structure_summary", "")),
            lifecycle_stage=str(data.get("lifecycle_stage", "")),
            prosperity_score=_as_float(data.get("prosperity_score")),
            sector_score=_as_float(data.get("sector_score")),
            core_targets=[Ticker.from_dict(t) for t in (data.get("core_targets") or [])],
            evidence_ids=[str(item) for item in (data.get("evidence_ids") or [])],
            evidence_state=str(data.get("evidence_state", "missing")),
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
    refresh_job_id: str = ""  # durable launch job that created swarm_run_id
    refresh_schedule: str = ""  # "" | "weekly" | "monthly"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    overview: ChainOverview = field(default_factory=ChainOverview)
    segments: List[Segment] = field(default_factory=list)
    # Structured research graph.  Unknown legacy payloads remain readable,
    # while new ingests require the schema validated before they reach here.
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    as_of: str = ""
    research_version: int = 0
    row_version: int = 0
    last_error: str = ""

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
            refresh_job_id=str(data.get("refresh_job_id", "")),
            refresh_schedule=str(data.get("refresh_schedule", "")),
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or _now_iso()),
            overview=ChainOverview.from_dict(data.get("overview") or {}),
            segments=[Segment.from_dict(s) for s in (data.get("segments") or [])],
            nodes=[dict(item) for item in (data.get("nodes") or []) if isinstance(item, dict)],
            edges=[dict(item) for item in (data.get("edges") or []) if isinstance(item, dict)],
            evidence=[dict(item) for item in (data.get("evidence") or []) if isinstance(item, dict)],
            conflicts=[dict(item) for item in (data.get("conflicts") or []) if isinstance(item, dict)],
            as_of=str(data.get("as_of", "")),
            research_version=int(data.get("research_version", 0) or 0),
            row_version=int(data.get("row_version", 0) or 0),
            last_error=str(data.get("last_error", "")),
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
            "as_of": self.as_of,
            "research_version": self.research_version,
            "row_version": self.row_version,
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
    """Industry-chain state on the shared SQLite WAL database.

    ``root`` remains a compatibility input for importing older per-directory
    JSON state and for boundary tests.  Supplying it never switches new writes
    back to files: an adjacent SQLite database remains the source of truth.
    """

    def __init__(
        self,
        root: Optional[Path] = None,
        *,
        database: Optional[StateDatabase] = None,
        database_path: Optional[Path] = None,
    ) -> None:
        self.root = root if root is not None else get_runtime_root() / "industry_chains"
        db_path = database_path or (self.root.parent / "state" / "vibe.db" if root else default_state_db_path())
        self.database = database or StateDatabase(db_path)
        self._legacy_checked = False

    def list_chains(self) -> List[Chain]:
        """Return all chains ordered by most recently updated state."""
        self._import_legacy_once()
        chains = [self._decode_chain(payload, version) for _, payload, version in self.database.list_records("industry_chain")]
        return [chain for chain in chains if chain is not None]

    def get_chain(self, chain_id: str) -> Optional[Chain]:
        """Return a chain and its authoritative optimistic-lock version."""
        self._chain_dir(chain_id)  # Validate before touching the database.
        self._import_legacy_once()
        record = self.database.get_record("industry_chain", chain_id)
        if record is None:
            return None
        return self._decode_chain(*record)

    def save_chain(self, chain: Chain, *, expected_version: Optional[int] = None) -> Chain:
        """Persist ``chain`` with compare-and-swap protection.

        A stale caller receives :class:`ConcurrentUpdateError` instead of
        silently replacing a newer research result or user edit.
        """
        self._chain_dir(chain.chain_id)
        self._import_legacy_once()
        chain.updated_at = _now_iso()
        expected = chain.row_version if expected_version is None else expected_version
        version = self.database.upsert_record(
            "industry_chain",
            chain.chain_id,
            chain.to_dict(),
            expected_version=expected if expected > 0 else 0,
        )
        chain.row_version = version
        return chain

    def delete_chain(self, chain_id: str) -> bool:
        """Delete current state plus its snapshots and refresh definition."""
        self._chain_dir(chain_id)
        self._import_legacy_once()
        deleted = self.database.delete_record("industry_chain", chain_id)
        self.database.delete_record("industry_chain_schedule", chain_id)
        for record_id, payload, _ in self.database.list_records("industry_chain_history"):
            if payload.get("chain_id") == chain_id:
                self.database.delete_record("industry_chain_history", record_id)
        return deleted

    def append_history(self, chain_id: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Write an immutable, versioned snapshot for comparison/audit."""
        self._chain_dir(chain_id)
        entry = {
            "snapshot_id": uuid.uuid4().hex,
            "chain_id": chain_id,
            "recorded_at": _now_iso(),
            **snapshot,
        }
        self.database.upsert_record(
            "industry_chain_history",
            f"{chain_id}:{entry['snapshot_id']}",
            entry,
            expected_version=0,
        )
        return entry

    def load_history(self, chain_id: str) -> List[Dict[str, Any]]:
        """Return immutable snapshots oldest first."""
        self._chain_dir(chain_id)
        self._import_legacy_once()
        snapshots = [
            dict(payload)
            for _, payload, _ in self.database.list_records("industry_chain_history")
            if payload.get("chain_id") == chain_id
        ]
        return sorted(snapshots, key=lambda item: str(item.get("recorded_at", "")))

    def get_snapshot(self, chain_id: str, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Return one snapshot, scoped to its owning chain."""
        self._chain_dir(chain_id)
        record = self.database.get_record("industry_chain_history", f"{chain_id}:{snapshot_id}")
        if record is None or record[0].get("chain_id") != chain_id:
            return None
        return dict(record[0])

    def save_schedule(
        self,
        chain_id: str,
        schedule: str,
        *,
        next_due_at: float | None,
        expected_version: int | None = None,
    ) -> Dict[str, Any]:
        """Persist refresh schedule separately from mutable chain content."""
        self._chain_dir(chain_id)
        current = self.database.get_record("industry_chain_schedule", chain_id)
        current_version = current[1] if current else 0
        payload = {
            "chain_id": chain_id,
            "schedule": schedule,
            "next_due_at": next_due_at,
            "updated_at": _now_iso(),
        }
        version = self.database.upsert_record(
            "industry_chain_schedule",
            chain_id,
            payload,
            expected_version=current_version if expected_version is None else expected_version,
        )
        payload["row_version"] = version
        return payload

    def get_schedule(self, chain_id: str) -> Optional[Dict[str, Any]]:
        self._chain_dir(chain_id)
        record = self.database.get_record("industry_chain_schedule", chain_id)
        if record is None:
            return None
        payload, version = record
        return {**payload, "row_version": version}

    def list_schedules(self) -> List[Dict[str, Any]]:
        return [{**payload, "row_version": version} for _, payload, version in self.database.list_records("industry_chain_schedule")]

    def _chain_dir(self, chain_id: str) -> Path:
        """Validate a chain identifier against the legacy filesystem root."""
        return resolve_within_root(self.root, chain_id, kind="chain_id")

    @staticmethod
    def _decode_chain(payload: Dict[str, Any], version: int) -> Optional[Chain]:
        try:
            chain = Chain.from_dict(payload)
            chain.row_version = version
            return chain
        except (TypeError, ValueError) as exc:
            logger.warning("Skipping corrupt industry-chain state: %s", exc)
            return None

    def _import_legacy_once(self) -> None:
        """One-time, non-destructive import of Node-8 JSON state."""
        if self._legacy_checked:
            return
        self._legacy_checked = True
        if self.database.list_records("industry_chain") or not self.root.exists():
            return
        for child in self.root.iterdir():
            if not child.is_dir() or child.is_symlink():
                continue
            chain = self._load_legacy_chain(child / _CHAIN_FILENAME)
            if chain is None:
                continue
            try:
                self.save_chain(chain, expected_version=0)
            except ConcurrentUpdateError:
                continue
            history_path = child / _HISTORY_FILENAME
            for snapshot in self._load_legacy_history(history_path):
                self.append_history(chain.chain_id, snapshot)

    @staticmethod
    def _load_legacy_chain(path: Path) -> Optional[Chain]:
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            return Chain.from_dict(envelope.get("chain", envelope))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Skipping legacy chain file %s: %s", path, exc)
            return None

    @staticmethod
    def _load_legacy_history(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            snapshots = envelope.get("snapshots", [])
            return [dict(item) for item in snapshots if isinstance(item, dict)]
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Skipping legacy history file %s: %s", path, exc)
            return []
