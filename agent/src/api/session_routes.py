"""Versioned session routes backed by the shared SessionService contract."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status

from src.contracts.sessions import (
    CreateSessionRequest,
    MessageResponse,
    SendMessageRequest,
    SessionResponse,
    UpdateSessionRequest,
)

AuthDependency = Callable[..., Awaitable[None]]
ServiceFactory = Callable[[], Any]
ShellToolsPolicy = Callable[[Request], bool]


def _service_or_501(factory: ServiceFactory) -> Any:
    service = factory()
    if service is None:
        raise HTTPException(status_code=501, detail="Session runtime not enabled")
    return service


def _session_response(session: Any) -> SessionResponse:
    return SessionResponse(
        session_id=session.session_id,
        title=session.title,
        status=session.status.value,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_attempt_id=session.last_attempt_id,
    )


def register_session_routes(
    app: FastAPI,
    *,
    require_auth: AuthDependency,
    get_service: ServiceFactory,
    shell_tools_enabled: ShellToolsPolicy,
) -> None:
    """Register the canonical ``/api/v1/sessions`` transport adapter."""

    router = APIRouter(
        prefix="/api/v1/sessions",
        tags=["v1", "sessions"],
        dependencies=[Depends(require_auth)],
    )

    @router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
    async def create_session(request: CreateSessionRequest) -> SessionResponse:
        session = _service_or_501(get_service).create_session(
            title=request.title,
            config=request.config,
        )
        return _session_response(session)

    @router.get("", response_model=list[SessionResponse])
    async def list_sessions(limit: int = Query(50, ge=1, le=200)) -> list[SessionResponse]:
        sessions = _service_or_501(get_service).list_sessions(limit=limit)
        return [_session_response(session) for session in sessions]

    @router.get("/{session_id}", response_model=SessionResponse)
    async def get_session(session_id: str) -> SessionResponse:
        service = _service_or_501(get_service)
        session = service.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        return _session_response(session)

    @router.patch("/{session_id}")
    async def update_session(session_id: str, request: UpdateSessionRequest) -> dict[str, str]:
        service = _service_or_501(get_service)
        session = service.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        if request.title is not None:
            session.title = request.title
        from datetime import datetime

        session.updated_at = datetime.now().isoformat()
        service.store.update_session(session)
        return {"status": "updated", "session_id": session_id}

    @router.delete("/{session_id}")
    async def delete_session(session_id: str) -> dict[str, str]:
        if not _service_or_501(get_service).delete_session(session_id):
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        return {"status": "deleted", "session_id": session_id}

    @router.post("/{session_id}/messages")
    async def send_message(
        session_id: str,
        payload: SendMessageRequest,
        request: Request,
    ) -> dict[str, str]:
        try:
            return await _service_or_501(get_service).send_message(
                session_id=session_id,
                content=payload.content,
                include_shell_tools=shell_tools_enabled(request),
                idempotency_key=request.headers.get("Idempotency-Key"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/{session_id}/messages", response_model=list[MessageResponse])
    async def get_messages(
        session_id: str,
        limit: int = Query(100, ge=1, le=1000),
    ) -> list[MessageResponse]:
        service = _service_or_501(get_service)
        if service.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        return [MessageResponse(**message.to_dict()) for message in service.get_messages(session_id, limit)]

    @router.post("/{session_id}/cancel")
    async def cancel_session(session_id: str) -> dict[str, str]:
        cancelled = _service_or_501(get_service).cancel_current(session_id)
        return {"status": "cancelled" if cancelled else "no_active_loop"}

    app.include_router(router)
