"""Scheduled research HTTP route boundary."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI, status


def register_research_routes(
    app: FastAPI,
    *,
    require_auth: Callable,
    response_model: type,
    create: Callable,
    list_all: Callable,
    delete: Callable,
) -> None:
    dependency = [Depends(require_auth)]
    app.add_api_route(
        "/scheduled-runs",
        create,
        methods=["POST"],
        response_model=response_model,
        status_code=status.HTTP_201_CREATED,
        dependencies=dependency,
        tags=["research"],
    )
    app.add_api_route(
        "/scheduled-runs",
        list_all,
        methods=["GET"],
        response_model=list[response_model],
        dependencies=dependency,
        tags=["research"],
    )
    app.add_api_route(
        "/scheduled-runs/{job_id}",
        delete,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=dependency,
        tags=["research"],
    )
