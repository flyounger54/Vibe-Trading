"""Versioned transport-neutral request and response contracts."""

from src.contracts.errors import ContractError, ErrorEnvelope
from src.contracts.goals import CreateGoalRequest, GoalApplicationService, GoalSnapshotResponse

__all__ = [
    "ContractError",
    "CreateGoalRequest",
    "ErrorEnvelope",
    "GoalApplicationService",
    "GoalSnapshotResponse",
]
