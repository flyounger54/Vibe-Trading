"""Install versioned aliases for legacy FastAPI routes without duplicating handlers."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute


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
        if route.path.startswith(prefix) or route.path in {"/openapi.json", "/docs", "/redoc"}:
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
            responses=route.responses,
            deprecated=route.deprecated,
            name=f"v1_{route.name}",
            operation_id=f"v1_{route.operation_id}" if route.operation_id else None,
            response_class=route.response_class,
            include_in_schema=route.include_in_schema,
        )
        existing_paths.add(versioned_path)
        installed += 1
    return installed
