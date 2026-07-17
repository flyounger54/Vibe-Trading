"""Live-channel authorization, mandate and runner HTTP route boundary."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI


def register_live_routes(
    app: FastAPI,
    *,
    require_auth: Callable,
    status_response_model: type,
    commit_mandate: Callable,
    halt: Callable,
    resume: Callable,
    status: Callable,
    authorize: Callable,
    start_runner: Callable,
    stop_runner: Callable,
) -> None:
    dependency = [Depends(require_auth)]
    routes = (
        ("/mandate/commit", commit_mandate, "POST", None),
        ("/live/halt", halt, "POST", None),
        ("/live/resume", resume, "POST", None),
        ("/live/status", status, "GET", status_response_model),
        ("/live/authorize", authorize, "POST", None),
        ("/live/runner/start", start_runner, "POST", None),
        ("/live/runner/stop", stop_runner, "POST", None),
    )
    for path, endpoint, method, response_model in routes:
        app.add_api_route(
            path,
            endpoint,
            methods=[method],
            response_model=response_model,
            dependencies=dependency,
            tags=["live"],
        )
