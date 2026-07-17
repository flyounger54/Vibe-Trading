"""Session HTTP/service contracts used by all transport adapters."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    title: str = Field("", description="Session title", max_length=500)
    config: Optional[Dict[str, Any]] = Field(None, description="Session config")


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=500)


class SessionResponse(BaseModel):
    session_id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    last_attempt_id: Optional[str] = None


class SendMessageRequest(BaseModel):
    content: str = Field(
        ...,
        description="Natural language strategy description",
        min_length=1,
        max_length=5000,
    )


class MessageResponse(BaseModel):
    message_id: str
    session_id: str
    role: str
    content: str
    created_at: str
    linked_attempt_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
