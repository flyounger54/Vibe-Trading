"""Tests for strategy zoo API endpoints."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.strategy_routes import register_strategy_routes


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    register_strategy_routes(app)
    return TestClient(app)


class TestStrategyListEndpoint:
    def test_list_all(self, client: TestClient) -> None:
        r = client.get("/strategy/list")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 40
        assert len(data["strategies"]) == 40

    def test_list_filter_category(self, client: TestClient) -> None:
        r = client.get("/strategy/list?category=trend")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 5
        for s in data["strategies"]:
            assert s["category"] == "trend"

    def test_list_filter_universe(self, client: TestClient) -> None:
        r = client.get("/strategy/list?universe=crypto")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] > 0
        for s in data["strategies"]:
            assert "crypto" in s["universe"]

    def test_list_filter_risk(self, client: TestClient) -> None:
        r = client.get("/strategy/list?risk=low")
        assert r.status_code == 200
        for s in r.json()["strategies"]:
            assert s["risk_profile"] == "low"

    def test_list_limit(self, client: TestClient) -> None:
        r = client.get("/strategy/list?limit=5")
        assert r.status_code == 200
        assert len(r.json()["strategies"]) == 5

    def test_list_strategy_fields(self, client: TestClient) -> None:
        r = client.get("/strategy/list?limit=1")
        s = r.json()["strategies"][0]
        assert "id" in s
        assert "category" in s
        assert "nickname" in s
        assert "universe" in s
        assert "risk_profile" in s
        assert "default_params" in s


class TestStrategyDetailEndpoint:
    def test_get_existing(self, client: TestClient) -> None:
        r = client.get("/strategy/trend_dual_ma")
        assert r.status_code == 200
        data = r.json()
        assert data["strategy"]["id"] == "trend_dual_ma"
        assert "SignalEngine" in data["source_code"]

    def test_get_nonexistent(self, client: TestClient) -> None:
        r = client.get("/strategy/nonexistent_xyz")
        assert r.status_code == 404

    def test_detail_meta_structure(self, client: TestClient) -> None:
        r = client.get("/strategy/mr_bollinger")
        data = r.json()
        meta = data["strategy"]["meta"]
        assert meta["category"] == "mean_reversion"
        assert "universe" in meta
        assert "default_params" in meta
