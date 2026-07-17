"""Strategy registry: AST-scan zoo modules, validate metadata, lazy-import.

Design contract (mirrors ``src/factors/registry.py``):
    StrategyMeta (pydantic, ``extra="forbid", frozen=True``)
    StrategyRegistry.list(category=None, universe=None, risk=None, directly_runnable=None) -> list[str]
    StrategyRegistry.get(strategy_id) -> Strategy
    StrategyRegistry.load(strategy_id, **params) -> SignalEngine instance
    StrategyRegistry.health() -> dict
    StrategyRegistry.export_manifest() -> dict

Source of truth = ``__strategy_meta__`` dict literal in each
``zoo/<category>/<id>.py``.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import logging
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.strategies.base import Strategy

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_MAX_PY_BYTES = 200_000

Category = Literal[
    "trend",
    "mean_reversion",
    "momentum",
    "multi_factor",
    "stat_arb",
    "event_driven",
    "volatility",
    "allocation",
    "crypto",
    "options",
]

Universe = Literal["equity_us", "equity_cn", "equity_hk", "crypto", "futures"]

RiskProfile = Literal["low", "medium", "high"]


class StrategyMeta(BaseModel):
    """Strict metadata schema; matches the ``__strategy_meta__`` dict literal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    nickname: str
    category: Category
    description: str
    universe: list[Universe]
    frequency: list[str]
    columns_required: list[str]
    default_params: dict[str, Any] = Field(default_factory=dict)
    required_params: list[str] = Field(default_factory=list)
    required_any_of: list[str] = Field(default_factory=list)
    configuration_requirements: list[str] = Field(default_factory=list)
    directly_runnable: bool = True
    risk_profile: RiskProfile
    min_bars: int = Field(ge=0)
    reference: str = ""
    factors_used: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_run_contract(self) -> "StrategyMeta":
        """Require an actionable explanation for non-default strategies."""
        if not self.directly_runnable and not (
            self.required_params
            or self.required_any_of
            or self.configuration_requirements
        ):
            raise ValueError(
                "non-default-runnable strategies require parameter or configuration metadata"
            )
        return self


class RegistryError(Exception):
    """Raised on registry-level configuration errors."""


class StrategyConfigurationError(RegistryError):
    """Raised when a strategy needs explicit configuration before it can run."""


@dataclass(frozen=True, slots=True)
class _LoadError:
    strategy_id: str
    reason: str


def _validate_id_token(token: str, kind: str) -> None:
    if not _ID_RE.fullmatch(token):
        raise RegistryError(f"invalid {kind} {token!r}: must match {_ID_RE.pattern}")


def _has_configured_value(value: Any) -> bool:
    """Return whether a parameter is explicitly usable as a runtime setting."""
    return value is not None and value != ""


def load_strategy_meta_from_py(path: Path) -> StrategyMeta:
    """AST-extract ``__strategy_meta__`` from a zoo module (no import)."""
    size = path.stat().st_size
    if size > _MAX_PY_BYTES:
        raise RegistryError(f"{path.name}: {size}B exceeds {_MAX_PY_BYTES}B cap")

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    meta_node: ast.expr | None = None
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        targets = [t for t in stmt.targets if isinstance(t, ast.Name)]
        if any(t.id == "__strategy_meta__" for t in targets):
            meta_node = stmt.value
            break

    if meta_node is None:
        raise RegistryError(f"{path.name}: __strategy_meta__ assignment not found")

    try:
        raw = ast.literal_eval(meta_node)
    except (ValueError, SyntaxError) as exc:
        raise RegistryError(f"{path.name}: __strategy_meta__ not a literal: {exc}") from exc

    if not isinstance(raw, dict):
        raise RegistryError(
            f"{path.name}: __strategy_meta__ must be dict, got {type(raw).__name__}"
        )

    try:
        return StrategyMeta(**raw)
    except ValidationError as exc:
        raise RegistryError(f"{path.name}: StrategyMeta validation failed: {exc}") from exc


def _zoo_dir_default() -> Path:
    return Path(__file__).parent / "zoo"


class StrategyRegistry:
    """In-memory registry of all discoverable strategies across zoo subdirectories."""

    def __init__(self, zoo_root: Path | None = None) -> None:
        default_root = _zoo_dir_default()
        self._zoo_root = (zoo_root or default_root).resolve()
        self._use_filesystem_loader = self._zoo_root != default_root.resolve()
        self._py_paths: dict[str, Path] = {}
        self._strategies: dict[str, Strategy] = {}
        self._load_errors: list[_LoadError] = []
        self._scan()

    def _scan(self) -> None:
        if not self._zoo_root.is_dir():
            return
        for cat_dir in sorted(self._zoo_root.iterdir()):
            if not cat_dir.is_dir():
                continue
            cat_id = cat_dir.name
            if cat_id.startswith("_") or cat_id == "__pycache__":
                continue
            for py_file in sorted(cat_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                self._try_register(cat_id, py_file)

    def _try_register(self, cat_id: str, py_file: Path) -> None:
        short_id = py_file.stem
        try:
            meta = load_strategy_meta_from_py(py_file)
        except RegistryError as exc:
            self._load_errors.append(_LoadError(f"{cat_id}.{short_id}", str(exc)))
            return

        module_path = f"src.strategies.zoo.{cat_id}.{short_id}"
        strategy = Strategy(
            id=meta.id,
            category=cat_id,
            module_path=module_path,
            meta=meta.model_dump(),
        )
        if strategy.id in self._strategies:
            self._load_errors.append(_LoadError(strategy.id, "duplicate strategy id"))
            return
        self._strategies[strategy.id] = strategy
        self._py_paths[strategy.id] = py_file

    # ----------------------------- public API -----------------------------

    def list(
        self,
        category: str | None = None,
        universe: str | None = None,
        risk: str | None = None,
        directly_runnable: bool | None = None,
    ) -> list[str]:
        """Return strategy IDs matching the (optional) filters."""
        out: list[str] = []
        for s in self._strategies.values():
            if category is not None and s.category != category:
                continue
            if universe is not None and universe not in s.meta.get("universe", []):
                continue
            if risk is not None and s.meta.get("risk_profile") != risk:
                continue
            if (
                directly_runnable is not None
                and bool(s.meta.get("directly_runnable", True)) is not directly_runnable
            ):
                continue
            out.append(s.id)
        return sorted(out)

    def list_default_runnable(self) -> list[str]:
        """Return strategies that can generate meaningful signals with defaults."""
        return self.list(directly_runnable=True)

    def get(self, strategy_id: str) -> Strategy:
        if strategy_id not in self._strategies:
            raise KeyError(f"strategy_id {strategy_id!r} not in registry")
        return self._strategies[strategy_id]

    def get_source(self, strategy_id: str) -> str:
        """Return the raw .py source of a registered strategy."""
        if strategy_id not in self._strategies:
            raise KeyError(f"strategy_id {strategy_id!r} not in registry")
        py_path = self._py_paths.get(strategy_id)
        if py_path is None:
            raise RegistryError(f"{strategy_id}: no source path recorded")
        try:
            size = py_path.stat().st_size
        except OSError as exc:
            raise RegistryError(f"{strategy_id}: cannot stat source: {exc}") from exc
        if size > _MAX_PY_BYTES:
            raise RegistryError(
                f"{strategy_id}: source {size}B exceeds {_MAX_PY_BYTES}B cap"
            )
        try:
            return py_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RegistryError(f"{strategy_id}: cannot read source: {exc}") from exc

    def validate_params(self, strategy_id: str, **params: Any) -> dict[str, Any]:
        """Merge parameters and reject missing declared prerequisites early."""
        strategy = self.get(strategy_id)
        meta = strategy.meta
        merged_params = {**meta.get("default_params", {}), **params}

        missing = [
            name
            for name in meta.get("required_params", [])
            if not _has_configured_value(merged_params.get(name))
        ]
        any_of = meta.get("required_any_of", [])
        missing_any_of = bool(any_of) and not any(
            _has_configured_value(merged_params.get(name)) for name in any_of
        )
        if missing or missing_any_of:
            details: list[str] = []
            if missing:
                details.append("required parameters: " + ", ".join(missing))
            if missing_any_of:
                details.append("one of: " + ", ".join(any_of))
            requirements = meta.get("configuration_requirements", [])
            if requirements:
                details.append("configuration: " + "; ".join(requirements))
            raise StrategyConfigurationError(
                f"{strategy_id}: cannot run with defaults; " + " | ".join(details)
            )
        return merged_params

    def load(self, strategy_id: str, **params: Any) -> Any:
        """Lazy-import the strategy module and instantiate its ``SignalEngine``.

        Override default_params with caller-supplied ``params``.
        """
        strategy = self.get(strategy_id)
        merged_params = self.validate_params(strategy_id, **params)

        try:
            module = self._load_module(strategy)
        except Exception as exc:  # noqa: BLE001
            raise RegistryError(f"{strategy_id}: import failed: {exc}") from exc

        engine_cls = getattr(module, "SignalEngine", None)
        if engine_cls is None:
            raise RegistryError(f"{strategy_id}: module has no SignalEngine class")

        try:
            return engine_cls(**merged_params)
        except Exception as exc:  # noqa: BLE001
            raise RegistryError(
                f"{strategy_id}: SignalEngine(**{merged_params}) failed: {exc}"
            ) from exc

    def health(self) -> dict[str, Any]:
        return {
            "loaded": len(self._strategies),
            "failed": len(self._load_errors),
            "errors": [
                {"strategy_id": e.strategy_id, "reason": e.reason}
                for e in self._load_errors
            ],
        }

    def export_manifest(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot for wiki / frontend."""
        from datetime import datetime, timezone

        categories: dict[str, list[dict[str, Any]]] = {}
        for s in self._strategies.values():
            categories.setdefault(s.category, []).append(
                {
                    "id": s.id,
                    "module_path": s.module_path,
                    "meta": s.meta,
                }
            )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "categories": [
                {
                    "category": cat_id,
                    "strategies": sorted(items, key=lambda x: x["id"]),
                }
                for cat_id, items in sorted(categories.items())
            ],
            "health": self.health(),
        }

    def _load_module(self, strategy: Strategy) -> Any:
        if not self._use_filesystem_loader:
            return importlib.import_module(strategy.module_path)
        py_file = self._py_paths[strategy.id]
        cached = sys.modules.get(strategy.module_path)
        if cached is not None and getattr(cached, "__file__", None) == str(py_file):
            return cached
        spec = importlib.util.spec_from_file_location(strategy.module_path, py_file)
        if spec is None or spec.loader is None:
            raise RegistryError(
                f"{strategy.id}: could not build import spec for {py_file}"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[strategy.module_path] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(strategy.module_path, None)
            raise
        return module


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_registry_cache: StrategyRegistry | None = None
_registry_cache_lock = threading.Lock()


def get_default_registry() -> StrategyRegistry:
    """Return a process-wide cached ``StrategyRegistry`` for the bundled zoo."""
    global _registry_cache
    with _registry_cache_lock:
        if _registry_cache is None:
            _registry_cache = StrategyRegistry()
        return _registry_cache


def reset_default_registry() -> None:
    """Drop the cached registry (test hook)."""
    global _registry_cache
    with _registry_cache_lock:
        _registry_cache = None
