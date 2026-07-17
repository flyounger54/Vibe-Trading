"""Feature and label Parquet cache with incremental computation."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path.home() / ".vibe-trading" / "cache" / "ml"


@dataclass
class CacheManifest:
    cache_key: str
    zoo: str
    universe: str
    factor_ids: list[str]
    date_range: tuple[str, str]
    n_dates: int
    n_codes: int
    content_hash: str
    created_at: str
    last_updated: str

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> CacheManifest:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["date_range"] = tuple(data["date_range"])
        return cls(**data)


class FeatureCache:
    """Parquet-based feature matrix cache with incremental append."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = (cache_dir or _DEFAULT_CACHE_DIR) / "features"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get_or_compute(
        self,
        panel: dict[str, pd.DataFrame],
        factor_ids: list[str],
        zoo: str,
        universe: str,
        compute_fn: Any | None = None,
    ) -> tuple[pd.DataFrame, bool]:
        """Return (feature_matrix, cache_hit).

        On full hit: reads from cache.
        On partial hit: computes only missing dates, appends to cache.
        On miss: computes all, writes cache.
        """
        cache_key = self._make_key(zoo, universe, factor_ids)
        content_hash = self._compute_content_hash(zoo, factor_ids)
        cache_path = self._cache_dir / cache_key
        manifest_path = cache_path / "manifest.json"

        if manifest_path.exists():
            manifest = CacheManifest.load(manifest_path)
            if manifest.content_hash != content_hash:
                logger.info("Content hash changed for %s, invalidating cache", cache_key)
                self.invalidate(cache_key)
            else:
                cached_df = self._read_cache(cache_path)
                if cached_df is not None:
                    panel_dates = self._get_panel_dates(panel)
                    cached_dates = set(cached_df.index.get_level_values("date").unique())
                    missing_dates = sorted(set(panel_dates) - cached_dates)

                    if not missing_dates:
                        requested_dates = set(panel_dates)
                        result = cached_df.loc[
                            cached_df.index.get_level_values("date").isin(requested_dates)
                        ]
                        logger.info("Cache full hit: %s (%d dates)", cache_key, len(requested_dates))
                        return result, True

                    logger.info(
                        "Cache partial hit: %s (%d missing dates)",
                        cache_key, len(missing_dates),
                    )
                    new_features = self._compute_for_dates(
                        panel, factor_ids, missing_dates, compute_fn
                    )
                    if new_features is not None and not new_features.empty:
                        combined = pd.concat([cached_df, new_features]).sort_index()
                        combined = combined[~combined.index.duplicated(keep="last")]
                        self._write_cache(cache_path, combined, zoo, universe, factor_ids, content_hash)
                        return combined, False

        logger.info("Cache miss: computing all factors for %s", cache_key)
        if compute_fn is not None:
            features = compute_fn(panel, factor_ids, zoo)
        else:
            from src.ml.features import build_feature_matrix
            features = build_feature_matrix(panel, factor_ids=factor_ids, zoo=zoo)

        if not features.empty:
            self._write_cache(cache_path, features, zoo, universe, factor_ids, content_hash)

        return features, False

    def invalidate(self, cache_key: str) -> None:
        import shutil
        cache_path = self._cache_dir / cache_key
        if cache_path.exists():
            shutil.rmtree(cache_path)
            logger.info("Invalidated cache: %s", cache_key)

    def _make_key(self, zoo: str, universe: str, factor_ids: list[str]) -> str:
        raw = f"{zoo}:{universe}:{','.join(sorted(factor_ids))}"
        return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()[:12]

    def _compute_content_hash(self, zoo: str, factor_ids: list[str]) -> str:
        """Hash factor source code to detect definition changes."""
        try:
            from src.factors.registry import get_default_registry
            registry = get_default_registry()
            sources = []
            for fid in sorted(factor_ids):
                try:
                    sources.append(registry.get_source(fid))
                except Exception:
                    sources.append(fid)
            return hashlib.md5("".join(sources).encode(), usedforsecurity=False).hexdigest()[:16]
        except Exception:
            return hashlib.md5(",".join(sorted(factor_ids)).encode(), usedforsecurity=False).hexdigest()[:16]

    def _get_panel_dates(self, panel: dict[str, pd.DataFrame]) -> list:
        for df in panel.values():
            if df is not None and not df.empty:
                return sorted(df.index.unique().tolist())
        return []

    def _compute_for_dates(
        self,
        panel: dict[str, pd.DataFrame],
        factor_ids: list[str],
        dates: list,
        compute_fn: Any | None,
    ) -> pd.DataFrame | None:
        sliced_panel = {}
        for key, df in panel.items():
            if df is not None and not df.empty:
                sliced_panel[key] = df.loc[df.index.isin(dates)]

        if not any(not df.empty for df in sliced_panel.values()):
            return None

        if compute_fn is not None:
            return compute_fn(sliced_panel, factor_ids, None)

        from src.ml.features import build_feature_matrix
        return build_feature_matrix(sliced_panel, factor_ids=factor_ids)

    def _read_cache(self, cache_path: Path) -> pd.DataFrame | None:
        parquet_files = sorted(cache_path.glob("*.parquet"))
        if not parquet_files:
            return None
        try:
            dfs = [pd.read_parquet(f) for f in parquet_files]
            return pd.concat(dfs).sort_index()
        except Exception as exc:
            logger.warning("Failed to read cache: %s", exc)
            return None

    def _write_cache(
        self,
        cache_path: Path,
        features: pd.DataFrame,
        zoo: str,
        universe: str,
        factor_ids: list[str],
        content_hash: str,
    ) -> None:
        cache_path.mkdir(parents=True, exist_ok=True)

        dates = features.index.get_level_values("date")
        for year, group in features.groupby(dates.year):
            group.to_parquet(cache_path / f"{year}.parquet")

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        unique_dates = dates.unique()
        unique_codes = features.index.get_level_values("code").unique()

        manifest = CacheManifest(
            cache_key=cache_path.name,
            zoo=zoo,
            universe=universe,
            factor_ids=sorted(factor_ids),
            date_range=(str(unique_dates.min()), str(unique_dates.max())),
            n_dates=len(unique_dates),
            n_codes=len(unique_codes),
            content_hash=content_hash,
            created_at=now,
            last_updated=now,
        )
        manifest.save(cache_path / "manifest.json")


class LabelCache:
    """Parquet-based label cache with incremental append."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = (cache_dir or _DEFAULT_CACHE_DIR) / "labels"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get_or_compute(
        self,
        panel: dict[str, pd.DataFrame],
        config: Any,
        universe: str,
    ) -> tuple[pd.Series, bool]:
        """Return (labels, cache_hit)."""
        from src.ml.labels import build_labels

        cache_key = self._make_key(config, universe)
        cache_path = self._cache_dir / cache_key
        parquet_path = cache_path / "labels.parquet"

        if parquet_path.exists():
            try:
                cached = pd.read_parquet(parquet_path).squeeze()
                if isinstance(cached, pd.Series):
                    cached.index.names = ["date", "code"]
                    return cached, True
            except Exception as exc:
                logger.warning("Failed to read label cache: %s", exc)

        labels = build_labels(panel, config)

        cache_path.mkdir(parents=True, exist_ok=True)
        labels.to_frame().to_parquet(parquet_path)

        return labels, False

    def _make_key(self, config: Any, universe: str) -> str:
        raw = f"{config.horizon}:{config.label_type}:{config.benchmark}:{config.cost_bps}:{universe}"
        return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()[:12]
