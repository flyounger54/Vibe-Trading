"""Transport-neutral contracts and application service for research goals."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.contracts.errors import ContractError, ErrorCode
from src.goal import GoalStatus, GoalStore, RiskTier
from src.goal.context import default_goal_criteria


class CreateGoalRequest(BaseModel):
    """Create or replace a finance research goal."""

    objective: str = Field(..., min_length=1, max_length=5000)
    criteria: list[str] = Field(default_factory=list)
    ui_summary: str = ""
    protocol: str = "thesis_review"
    risk_tier: str = RiskTier.RESEARCH_GENERAL.value
    token_budget: int | None = Field(None, ge=1)
    turn_budget: int | None = Field(None, ge=1)
    time_budget_seconds: int | None = Field(None, ge=1)


class GoalRecordResponse(BaseModel):
    goal_id: str
    session_id: str
    status: GoalStatus
    objective: str
    ui_summary: str
    source: str
    protocol: str
    risk_tier: RiskTier
    token_budget: int | None = None
    tokens_used: int
    turn_budget: int | None = None
    turns_used: int
    time_budget_seconds: int | None = None
    time_used_seconds: int
    budget_wrapup_sent: bool
    created_at: str
    updated_at: str
    completed_at: str | None = None
    recap: str | None = None


class GoalClaimResponse(BaseModel):
    claim_id: str
    goal_id: str
    session_id: str
    claim_type: str
    text: str
    status: str
    created_at: str
    updated_at: str


class GoalCriterionResponse(BaseModel):
    criterion_id: str
    goal_id: str
    session_id: str
    text: str
    required: bool
    status: str
    freshness_requirement: str | None = None
    protocol_step: str | None = None
    created_at: str
    updated_at: str


class GoalEvidenceResponse(BaseModel):
    evidence_id: str
    goal_id: str
    session_id: str
    text: str
    criterion_id: str | None = None
    claim_id: str | None = None
    evidence_type: str
    tool_call_id: str | None = None
    run_id: str | None = None
    source_provider: str | None = None
    source_type: str | None = None
    source_uri: str | None = None
    symbol_universe: list[str]
    benchmark: list[str]
    timeframe: str | None = None
    method: str | None = None
    assumptions: dict[str, Any]
    artifact_path: str | None = None
    artifact_hash: str | None = None
    retrieved_at: str
    data_as_of: str | None = None
    freshness_status: str
    verification_status: str
    confidence: str | None = None
    caveat: str | None = None
    contradicts_claim_ids: list[str]
    created_at: str


class GoalSnapshotResponse(BaseModel):
    """Stable JSON-safe research goal response."""

    goal: GoalRecordResponse
    claims: list[GoalClaimResponse]
    criteria: list[GoalCriterionResponse]
    evidence: list[GoalEvidenceResponse]
    evidence_count: int = 0


class GoalApplicationService:
    """Apply goal commands identically for HTTP, CLI, MCP and agent tools."""

    def __init__(self, store: GoalStore) -> None:
        self.store = store

    def create(self, *, session_id: str, request: CreateGoalRequest, source: str) -> dict[str, Any]:
        """Create a goal and return its fully reloaded snapshot.

        All user-caused validation failures are translated to one stable error
        code here so transport adapters cannot drift.
        """
        try:
            risk_tier = RiskTier(request.risk_tier)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_ARGUMENT,
                f"invalid risk_tier: {request.risk_tier}",
            ) from exc

        criteria = [item.strip() for item in request.criteria if item and item.strip()]
        if not criteria:
            criteria = default_goal_criteria()

        try:
            goal = self.store.replace_goal(
                session_id=session_id.strip(),
                objective=request.objective,
                criteria=criteria,
                ui_summary=request.ui_summary,
                source=source,
                protocol=request.protocol,
                risk_tier=risk_tier,
                token_budget=request.token_budget,
                turn_budget=request.turn_budget,
                time_budget_seconds=request.time_budget_seconds,
            )
        except (TypeError, ValueError) as exc:
            raise ContractError(ErrorCode.INVALID_ARGUMENT, str(exc)) from exc

        snapshot = self.store.get_goal_snapshot(goal.goal_id)
        if snapshot is None:
            raise ContractError(ErrorCode.INTERNAL, "Goal created but could not be reloaded")
        return snapshot
