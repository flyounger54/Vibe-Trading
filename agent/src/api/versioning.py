"""Install versioned aliases for legacy FastAPI routes without duplicating handlers."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

from src.contracts.errors import ErrorEnvelope


V1_ERROR_RESPONSES = {
    code: {"model": ErrorEnvelope, "description": description}
    for code, description in {
        400: "Invalid request",
        401: "Authentication required",
        403: "Request forbidden",
        404: "Resource not found",
        409: "Request conflict",
        422: "Request validation failed",
        429: "Rate limit exceeded",
        500: "Internal server error",
        502: "Upstream service error",
        503: "Service unavailable",
        504: "Upstream service timeout",
    }.items()
}


def install_version_aliases(app: FastAPI, *, prefix: str = "/api/v1") -> int:
    """Expose each business route below ``prefix`` and keep legacy paths active."""

    installed = 0
    original_routes = list(app.routes)
    existing_paths = {
        route.path for route in original_routes if isinstance(route, APIRoute)
    }
    for route in original_routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path.startswith(prefix):
            route.responses = {**route.responses, **V1_ERROR_RESPONSES}
            continue
        if route.path in {"/openapi.json", "/docs", "/redoc"}:
            continue
        versioned_path = f"{prefix}{route.path}"
        if versioned_path in existing_paths:
            continue
        app.add_api_route(
            versioned_path,
            route.endpoint,
            methods=list(route.methods or []),
            response_model=route.response_model,
            status_code=route.status_code,
            tags=["v1", *list(route.tags or [])],
            dependencies=list(route.dependencies or []),
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            responses={**route.responses, **V1_ERROR_RESPONSES},
            deprecated=route.deprecated,
            name=f"v1_{route.name}",
            operation_id=f"v1_{route.operation_id}" if route.operation_id else None,
            response_class=route.response_class,
            include_in_schema=route.include_in_schema,
        )
        existing_paths.add(versioned_path)
        installed += 1
    return installed
