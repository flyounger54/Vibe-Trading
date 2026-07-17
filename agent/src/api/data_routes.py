"""Market-data configuration HTTP route boundary."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI


def register_data_routes(
    app: FastAPI,
    *,
    require_read: Callable,
    require_write: Callable,
    response_model: type,
    get_settings: Callable,
    update_settings: Callable,
) -> None:
    app.add_api_route(
        "/settings/data-sources",
        get_settings,
        methods=["GET"],
        response_model=response_model,
        dependencies=[Depends(require_read)],
        tags=["data"],
    )
    app.add_api_route(
        "/settings/data-sources",
        update_settings,
        methods=["PUT"],
        response_model=response_model,
        dependencies=[Depends(require_write)],
        tags=["data"],
    )
