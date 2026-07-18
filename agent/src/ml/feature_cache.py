"""Versioned feature/label Parquet caches with leakage-safe invalidation."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path.home() / ".vibe-trading" / "cache" / "ml"
_FEATURE_CACHE_VERSION = "feature-v2"
_LABEL_CACHE_VERSION = "label-v2"
_DEFAULT_ROLLING_LOOKBACK_BARS = 252


def fingerprint_panel(panel: dict[str, pd.DataFrame]) -> str:
    """Return a deterministic fingerprint of every data value used by ML.

    A cache cannot be valid merely because it names the same universe: vendor
    corrections, revised corporate actions and changed date windows all alter
    training inputs.  ``hash_pandas_object`` includes values and index, while
    explicit field/schema markers make the result unambiguous.
    """
    digest = hashlib.sha256()
    for name in sorted(panel):
        frame = panel[name]
        digest.update(name.encode("utf-8"))
        if frame is None:
            digest.update(b"<none>")
            continue
        digest.update(str(tuple(frame.shape)).encode("utf-8"))
        digest.update("|".join(map(str, frame.columns)).encode("utf-8"))
        digest.update("|".join(map(str, frame.dtypes)).encode("utf-8"))
        try:
            values = pd.util.hash_pandas_object(frame, index=True).values
            digest.update(values.tobytes())
        except (TypeError, ValueError):
            # The JSON fallback is slower but still ensures a cache miss for
            # unusual object-typed frames rather than trusting stale data.
            digest.update(frame.to_json(date_format="iso", orient="split").encode("utf-8"))
    return digest.hexdigest()


def fingerprint_pit_members(pit_members: dict[str, list[str]] | None) -> str:
    if pit_members is None:
        return "pit-unavailable"
    canonical = {
        str(date): sorted(str(code) for code in members)
        for date, members in sorted(pit_members.items(), key=lambda item: str(item[0]))
    }
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _panel_date_range(panel: dict[str, pd.DataFrame]) -> tuple[str, str]:
    dates = []
    for frame in panel.values():
        if frame is not None and not frame.empty:
            dates.extend(frame.index.tolist())
    if not dates:
        return ("", "")
    ordered = pd.DatetimeIndex(dates).unique().sort_values()
    return (str(ordered.min()), str(ordered.max()))


def _slice_panel_to_dates(
    panel: dict[str, pd.DataFrame], dates: set[Any]
) -> dict[str, pd.DataFrame]:
    return {
        name: frame.loc[frame.index.isin(dates)] if frame is not None else None
        for name, frame in panel.items()
    }


def _slice_pit_to_dates(
    pit_members: dict[str, list[str]] | None, dates: list[Any]
) -> dict[str, list[str]] | None:
    if pit_members is None:
        return None
    wanted = {
        pd.Timestamp(date).strftime("%Y%m%d")
        for date in dates
    }
    return {
        str(date): members
        for date, members in pit_members.items()
        if str(date).replace("-", "")[:8] in wanted
    }


def _require_duckdb() -> Any:
    try:
        import duckdb
        return duckdb
    except ImportError as exc:
        raise RuntimeError(
            "ML Parquet caching requires the declared dependency 'duckdb'. "
            "Install agent requirements before enabling use_cache."
        ) from exc


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    """Write a MultiIndex frame through DuckDB's native Parquet engine."""
    duckdb = _require_duckdb()
    connection = duckdb.connect()
    try:
        connection.register("cache_frame", frame.reset_index())
        connection.execute("COPY cache_frame TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        connection.close()


def _read_parquet(path: Path) -> pd.DataFrame:
    """Read a native Parquet frame and restore the required ML index schema."""
    duckdb = _require_duckdb()
    connection = duckdb.connect()
    try:
        result = connection.execute("SELECT * FROM read_parquet(?)", [str(path)]).fetchdf()
    finally:
        connection.close()
    if "date" not in result.columns or "code" not in result.columns:
        raise ValueError("Parquet cache does not contain date and code index columns")
    result["date"] = pd.to_datetime(result["date"])
    return result.set_index(["date", "code"]).sort_index()


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
    cache_version: str = _FEATURE_CACHE_VERSION
    data_fingerprint: str = ""
    pit_fingerprint: str = "pit-unavailable"
    rolling_lookback_bars: int = _DEFAULT_ROLLING_LOOKBACK_BARS

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> CacheManifest:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["date_range"] = tuple(data["date_range"])
        # Old cache entries have insufficient provenance.  They intentionally
        # fail compatibility checks and are rebuilt instead of being reused.
        return cls(**data)


class FeatureCache:
    """Parquet feature cache keyed by data, PIT universe and factor version."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        rolling_lookback_bars: int = _DEFAULT_ROLLING_LOOKBACK_BARS,
    ) -> None:
        if rolling_lookback_bars < 0:
            raise ValueError("rolling_lookback_bars must be non-negative")
        self._cache_dir = (cache_dir or _DEFAULT_CACHE_DIR) / "features"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._rolling_lookback_bars = rolling_lookback_bars

    def get_or_compute(
        self,
        panel: dict[str, pd.DataFrame],
        factor_ids: list[str],
        zoo: str,
        universe: str,
        compute_fn: Any | None = None,
        pit_members: dict[str, list[str]] | None = None,
    ) -> tuple[pd.DataFrame, bool]:
        """Return a verified feature matrix, rebuilding corrupt/stale entries."""
        data_fingerprint = fingerprint_panel(panel)
        pit_fingerprint = fingerprint_pit_members(pit_members)
        factor_version = self._compute_content_hash(zoo, factor_ids)
        cache_key = self._make_key(
            zoo, universe, factor_ids, data_fingerprint, pit_fingerprint, factor_version
        )
        cache_path = self._cache_dir / cache_key
        manifest_path = cache_path / "manifest.json"

        if manifest_path.exists():
            try:
                manifest = CacheManifest.load(manifest_path)
                compatible = (
                    manifest.cache_version == _FEATURE_CACHE_VERSION
                    and manifest.data_fingerprint == data_fingerprint
                    and manifest.pit_fingerprint == pit_fingerprint
                    and manifest.content_hash == factor_version
                    and manifest.rolling_lookback_bars == self._rolling_lookback_bars
                )
                if compatible:
                    cached = self._read_cache(cache_path)
                    if cached is not None:
                        return cached, True
                logger.info("Invalidating stale feature cache %s", cache_key)
            except RuntimeError:
                raise
            except Exception as exc:
                logger.warning("Invalidating corrupt feature cache %s: %s", cache_key, exc)
            self.invalidate(cache_key)

        # A growing time series has a new full data fingerprint and therefore
        # a new cache key.  We may still reuse an *exactly verified historical
        # prefix* and compute only the new dates.  The prefix check prevents a
        # vendor revision from being mistaken for an append.
        incremental = self._find_verified_prefix(
            panel, factor_ids, zoo, universe, factor_version, pit_members
        )
        if incremental is not None:
            cached, missing_dates = incremental
            new_rows = self._compute_for_dates(
                panel, factor_ids, missing_dates, compute_fn, pit_members
            )
            if new_rows is not None and not new_rows.empty:
                features = pd.concat([cached, new_rows]).sort_index()
                features = features[~features.index.duplicated(keep="last")]
                self._write_cache(
                    cache_path,
                    features,
                    zoo,
                    universe,
                    factor_ids,
                    factor_version,
                    data_fingerprint,
                    pit_fingerprint,
                )
                logger.info("Feature cache verified-prefix reuse: %d new dates", len(missing_dates))
                return features, False

        features = self._compute(panel, factor_ids, zoo, compute_fn, pit_members)
        if not features.empty:
            self._write_cache(
                cache_path,
                features,
                zoo,
                universe,
                factor_ids,
                factor_version,
                data_fingerprint,
                pit_fingerprint,
            )
        return features, False

    def invalidate(self, cache_key: str) -> None:
        cache_path = self._cache_dir / cache_key
        if cache_path.exists():
            shutil.rmtree(cache_path)

    def _make_key(
        self,
        zoo: str,
        universe: str,
        factor_ids: list[str],
        data_fingerprint: str = "",
        pit_fingerprint: str = "pit-unavailable",
        factor_version: str = "",
    ) -> str:
        raw = json.dumps(
            {
                "version": _FEATURE_CACHE_VERSION,
                "zoo": zoo,
                "universe": universe,
                "factor_ids": sorted(factor_ids),
                "data_fingerprint": data_fingerprint,
                "pit_fingerprint": pit_fingerprint,
                "factor_version": factor_version,
                "rolling_lookback_bars": self._rolling_lookback_bars,
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

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
            return hashlib.sha256("".join(sources).encode("utf-8")).hexdigest()[:20]
        except Exception:
            return hashlib.sha256(",".join(sorted(factor_ids)).encode("utf-8")).hexdigest()[:20]

    def _compute(
        self,
        panel: dict[str, pd.DataFrame],
        factor_ids: list[str],
        zoo: str,
        compute_fn: Any | None,
        pit_members: dict[str, list[str]] | None,
    ) -> pd.DataFrame:
        if compute_fn is not None:
            return compute_fn(panel, factor_ids, zoo)
        from src.ml.features import build_feature_matrix

        return build_feature_matrix(
            panel, factor_ids=factor_ids, zoo=zoo, pit_members=pit_members
        )

    def _find_verified_prefix(
        self,
        panel: dict[str, pd.DataFrame],
        factor_ids: list[str],
        zoo: str,
        universe: str,
        factor_version: str,
        pit_members: dict[str, list[str]] | None,
    ) -> tuple[pd.DataFrame, list] | None:
        full_dates = self._get_panel_dates(panel)
        if len(full_dates) < 2:
            return None
        full_date_set = set(full_dates)
        for candidate_path in self._cache_dir.iterdir():
            manifest_path = candidate_path / "manifest.json"
            if not candidate_path.is_dir() or not manifest_path.exists():
                continue
            try:
                manifest = CacheManifest.load(manifest_path)
                if (
                    manifest.cache_version != _FEATURE_CACHE_VERSION
                    or manifest.zoo != zoo
                    or manifest.universe != universe
                    or manifest.factor_ids != sorted(factor_ids)
                    or manifest.content_hash != factor_version
                    or manifest.rolling_lookback_bars != self._rolling_lookback_bars
                ):
                    continue
                cached = self._read_cache(candidate_path)
                if cached is None:
                    continue
                cached_dates = sorted(cached.index.get_level_values("date").unique().tolist())
                if not cached_dates or not set(cached_dates) < full_date_set:
                    continue
                prefix_panel = _slice_panel_to_dates(panel, set(cached_dates))
                if fingerprint_panel(prefix_panel) != manifest.data_fingerprint:
                    continue
                prefix_pit = _slice_pit_to_dates(pit_members, cached_dates)
                if fingerprint_pit_members(prefix_pit) != manifest.pit_fingerprint:
                    continue
                missing_dates = [date for date in full_dates if date not in set(cached_dates)]
                return cached, missing_dates
            except RuntimeError:
                raise
            except Exception as exc:
                logger.warning("Ignoring invalid incremental feature cache %s: %s", candidate_path, exc)
        return None

    def _compute_for_dates(
        self,
        panel: dict[str, pd.DataFrame],
        factor_ids: list[str],
        dates: list,
        compute_fn: Any | None,
        pit_members: dict[str, list[str]] | None = None,
    ) -> pd.DataFrame | None:
        """Recompute requested dates with enough prior bars for rolling factors.

        This method is retained for incremental callers.  It never computes a
        rolling alpha on an isolated date slice, which would yield a different
        answer from a full-history calculation.
        """
        panel_dates = self._get_panel_dates(panel)
        if not panel_dates or not dates:
            return None
        wanted = set(dates)
        positions = [i for i, date in enumerate(panel_dates) if date in wanted]
        if not positions:
            return None
        start = max(0, min(positions) - self._rolling_lookback_bars)
        end = max(positions) + 1
        source_dates = set(panel_dates[start:end])
        sliced = {
            name: frame.loc[frame.index.isin(source_dates)]
            for name, frame in panel.items()
            if frame is not None and not frame.empty
        }
        result = self._compute(sliced, factor_ids, "", compute_fn, pit_members)
        if result is None:
            return None
        return result.loc[result.index.get_level_values("date").isin(wanted)]

    @staticmethod
    def _get_panel_dates(panel: dict[str, pd.DataFrame]) -> list:
        for frame in panel.values():
            if frame is not None and not frame.empty:
                return sorted(frame.index.unique().tolist())
        return []

    @staticmethod
    def _read_cache(cache_path: Path) -> pd.DataFrame | None:
        parquet_files = sorted(cache_path.glob("*.parquet"))
        if not parquet_files:
            return None
        dfs = [_read_parquet(path) for path in parquet_files]
        result = pd.concat(dfs).sort_index()
        if not isinstance(result.index, pd.MultiIndex) or result.index.nlevels != 2:
            raise ValueError("Feature cache does not contain a (date, code) MultiIndex")
        result.index.names = ["date", "code"]
        return result

    def _write_cache(
        self,
        cache_path: Path,
        features: pd.DataFrame,
        zoo: str,
        universe: str,
        factor_ids: list[str],
        factor_version: str,
        data_fingerprint: str,
        pit_fingerprint: str,
    ) -> None:
        cache_path.mkdir(parents=True, exist_ok=True)
        dates = pd.DatetimeIndex(features.index.get_level_values("date"))
        for year, group in features.groupby(dates.year):
            _write_parquet(cache_path / f"{year}.parquet", group)

        now = datetime.now(timezone.utc).isoformat()
        manifest = CacheManifest(
            cache_key=cache_path.name,
            zoo=zoo,
            universe=universe,
            factor_ids=sorted(factor_ids),
            date_range=(str(dates.min()), str(dates.max())),
            n_dates=len(dates.unique()),
            n_codes=len(features.index.get_level_values("code").unique()),
            content_hash=factor_version,
            created_at=now,
            last_updated=now,
            data_fingerprint=data_fingerprint,
            pit_fingerprint=pit_fingerprint,
            rolling_lookback_bars=self._rolling_lookback_bars,
        )
        manifest.save(cache_path / "manifest.json")


class LabelCache:
    """Versioned label cache with every label-defining field in the key."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = (cache_dir or _DEFAULT_CACHE_DIR) / "labels"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get_or_compute(
        self,
        panel: dict[str, pd.DataFrame],
        config: Any,
        universe: str,
    ) -> tuple[pd.Series, bool]:
        from src.ml.labels import build_labels

        data_fingerprint = fingerprint_panel(panel)
        dates = _panel_date_range(panel)
        cache_key = self._make_key(config, universe, data_fingerprint, dates)
        cache_path = self._cache_dir / cache_key
        parquet_path = cache_path / "labels.parquet"
        manifest_path = cache_path / "manifest.json"

        if parquet_path.exists() and manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    manifest.get("cache_version") == _LABEL_CACHE_VERSION
                    and manifest.get("data_fingerprint") == data_fingerprint
                    and tuple(manifest.get("date_range", ())) == dates
                ):
                    cached = _read_parquet(parquet_path).squeeze("columns")
                    if isinstance(cached, pd.Series) and isinstance(cached.index, pd.MultiIndex):
                        cached.index.names = ["date", "code"]
                        return cached, True
            except RuntimeError:
                raise
            except Exception as exc:
                logger.warning("Invalidating corrupt label cache %s: %s", cache_key, exc)
            shutil.rmtree(cache_path, ignore_errors=True)

        labels = build_labels(panel, config)
        cache_path.mkdir(parents=True, exist_ok=True)
        _write_parquet(parquet_path, labels.to_frame(name=labels.name or "label"))
        manifest_path.write_text(
            json.dumps(
                {
                    "cache_key": cache_key,
                    "cache_version": _LABEL_CACHE_VERSION,
                    "universe": universe,
                    "date_range": dates,
                    "data_fingerprint": data_fingerprint,
                    "label_config": {
                        "horizon": config.horizon,
                        "label_type": config.label_type,
                        "threshold": config.threshold,
                        "quantile_pct": config.quantile_pct,
                        "benchmark": config.benchmark,
                        "cost_bps": config.cost_bps,
                    },
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        return labels, False

    def _make_key(
        self,
        config: Any,
        universe: str,
        data_fingerprint: str = "",
        date_range: tuple[str, str] = ("", ""),
    ) -> str:
        raw = json.dumps(
            {
                "version": _LABEL_CACHE_VERSION,
                "universe": universe,
                "date_range": date_range,
                "data_fingerprint": data_fingerprint,
                "horizon": config.horizon,
                "label_type": config.label_type,
                "threshold": config.threshold,
                "quantile_pct": config.quantile_pct,
                "benchmark": config.benchmark,
                "cost_bps": config.cost_bps,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
