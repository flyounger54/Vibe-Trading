"""Validated evidence contract for industry-chain swarm output.

The dashboard accepts only this structured payload.  Free-form Markdown is
useful for analysts, but is not a safe database protocol: it cannot prove the
source, observation date, or conflict state behind a conclusion.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, model_validator

EvidenceState = Literal["supported", "stale", "conflicting", "missing"]
EvidenceStatus = Literal["active", "stale", "conflicting", "missing"]
Confidence = Literal["high", "medium", "low"]


class Evidence(BaseModel):
    """One source that can support or qualify one or more research claims."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{3,80}$")]
    claim: Annotated[str, Field(min_length=1, max_length=2000)]
    source_name: Annotated[str, Field(min_length=1, max_length=200)]
    source_url: HttpUrl | None = None
    as_of: datetime
    retrieved_at: datetime
    expires_at: datetime | None = None
    confidence: Confidence
    status: EvidenceStatus = "active"

    @model_validator(mode="after")
    def validate_source_lifecycle(self) -> "Evidence":
        if self.status != "missing" and self.source_url is None:
            raise ValueError("source_url is required unless evidence status is missing")
        if self.expires_at is not None and self.expires_at < self.as_of:
            raise ValueError("expires_at cannot precede as_of")
        return self


class ResearchNode(BaseModel):
    """A physical/economic node in the chain graph."""

    model_config = ConfigDict(extra="forbid")

    node_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{3,80}$")]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    node_type: Literal["material", "component", "equipment", "service", "company", "market"]
    description: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence = "low"


class ResearchEdge(BaseModel):
    """A directional upstream/downstream relation with explicit evidence."""

    model_config = ConfigDict(extra="forbid")

    edge_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{3,80}$")]
    upstream_id: str
    downstream_id: str
    relation: Literal["supplies", "enables", "competes_with", "depends_on"]
    description: Annotated[str, Field(min_length=1, max_length=1200)]
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence = "low"


class ResearchTicker(BaseModel):
    """A company observation within a segment."""

    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(min_length=1, max_length=40)]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    market: Literal["A", "US", "HK"] = "A"
    score: float | None = Field(default=None, ge=0, le=100)
    tier: Literal["Core", "Build", "Watch", "Skip", ""] = ""
    classification: Literal["Controller", "Integrator", "Beneficiary", ""] = ""
    key_products: str = ""
    red_team_note: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence = "low"


class ResearchSegment(BaseModel):
    """Structured dashboard view of one production-chain segment."""

    model_config = ConfigDict(extra="forbid")

    segment_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{3,80}$")]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    name_en: str = ""
    order: int = Field(ge=0, le=1000)
    positioning: Literal["上游", "中游", "下游", ""] = ""
    value_weight: str = ""
    localization_rate: str = ""
    international_competition: str = ""
    domestic_competition: str = ""
    barrier_type: str = ""
    barrier_description: str = ""
    chokepoint_score: dict[str, float] = Field(default_factory=dict)
    chokepoint_total: float | None = Field(default=None, ge=0, le=100)
    tickers: list[ResearchTicker] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence = "low"


class ResearchOverview(BaseModel):
    """Chain-level facts exposed by the overview card."""

    model_config = ConfigDict(extra="forbid")

    structure_summary: Annotated[str, Field(min_length=1, max_length=4000)]
    lifecycle_stage: Literal["Discovery", "Validation", "Mainstream", "Exhaustion", ""] = ""
    prosperity_score: float | None = Field(default=None, ge=0, le=100)
    sector_score: float | None = Field(default=None, ge=0, le=100)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence = "low"


class EvidenceConflict(BaseModel):
    """A live disagreement; it suppresses deterministic presentation."""

    model_config = ConfigDict(extra="forbid")

    conflict_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{3,80}$")]
    subject_id: Annotated[str, Field(min_length=1, max_length=160)]
    evidence_ids: list[str] = Field(min_length=2)
    description: Annotated[str, Field(min_length=1, max_length=2000)]
    status: Literal["open", "resolved"] = "open"


class IndustryResearchResult(BaseModel):
    """The sole machine-ingestible result accepted from a Swarm run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    as_of: datetime
    overview: ResearchOverview
    nodes: list[ResearchNode] = Field(default_factory=list)
    edges: list[ResearchEdge] = Field(default_factory=list)
    segments: list[ResearchSegment] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> "IndustryResearchResult":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique")
        known_evidence = set(evidence_ids)
        node_ids = {node.node_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("node_id values must be unique")
        segment_ids = {segment.segment_id for segment in self.segments}
        if len(segment_ids) != len(self.segments):
            raise ValueError("segment_id values must be unique")
        edge_ids = {edge.edge_id for edge in self.edges}
        if len(edge_ids) != len(self.edges):
            raise ValueError("edge_id values must be unique")
        for edge in self.edges:
            if edge.upstream_id not in node_ids or edge.downstream_id not in node_ids:
                raise ValueError(f"edge {edge.edge_id} references an unknown node")
        for evidence in self.evidence:
            if evidence.as_of > self.as_of:
                raise ValueError(f"evidence {evidence.evidence_id} is newer than result as_of")
        subjects = {"overview", *node_ids, *segment_ids}
        subjects.update(f"ticker:{ticker.code}" for segment in self.segments for ticker in segment.tickers)
        for conflict in self.conflicts:
            if conflict.subject_id not in subjects:
                raise ValueError(f"conflict {conflict.conflict_id} references an unknown subject")
        references: list[tuple[str, list[str]]] = [("overview", self.overview.evidence_ids)]
        references.extend((node.node_id, node.evidence_ids) for node in self.nodes)
        references.extend((edge.edge_id, edge.evidence_ids) for edge in self.edges)
        references.extend((segment.segment_id, segment.evidence_ids) for segment in self.segments)
        references.extend(
            (f"ticker:{ticker.code}", ticker.evidence_ids)
            for segment in self.segments
            for ticker in segment.tickers
        )
        references.extend((conflict.conflict_id, conflict.evidence_ids) for conflict in self.conflicts)
        for owner, refs in references:
            missing = set(refs) - known_evidence
            if missing:
                raise ValueError(f"{owner} references unknown evidence: {sorted(missing)}")
        return self

    def evidence_state(self, evidence_ids: list[str], subject_id: str = "") -> EvidenceState:
        """Resolve presentation state without promoting uncertain facts."""
        if not evidence_ids:
            return "missing"
        evidence_by_id = {item.evidence_id: item for item in self.evidence}
        selected = [evidence_by_id[item] for item in evidence_ids if item in evidence_by_id]
        now = datetime.now(timezone.utc)
        if not selected or any(item.status == "missing" for item in selected):
            return "missing"
        if any(item.status == "stale" or (item.expires_at is not None and item.expires_at < now) for item in selected):
            return "stale"
        if any(item.status == "conflicting" for item in selected):
            return "conflicting"
        if subject_id and any(
            conflict.status == "open" and conflict.subject_id == subject_id
            and set(conflict.evidence_ids).intersection(evidence_ids)
            for conflict in self.conflicts
        ):
            return "conflicting"
        return "supported"

    def to_storage_dict(self) -> dict[str, Any]:
        """Return JSON-safe data with evidence lifecycle states materialized."""
        return self.model_dump(mode="json")


def validate_research_result(data: object) -> IndustryResearchResult:
    """Validate a decoded Swarm payload and expose a stable domain exception."""
    try:
        return IndustryResearchResult.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"invalid industry research result: {exc}") from exc
