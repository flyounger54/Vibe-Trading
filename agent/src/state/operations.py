"""Backup, verification and recovery primitives for the durable SQLite state."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.state.database import StateDatabase


def database_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_database(path: Path) -> dict[str, Any]:
    """Verify a database without creating or migrating it."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0]).lower()
        row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return {
        "path": str(path),
        "integrity": integrity,
        "schema_version": int(row[0] or 0),
        "sha256": database_digest(path),
        "bytes": path.stat().st_size,
    }


def backup_database(source: Path, target: Path) -> dict[str, Any]:
    source = Path(source).resolve()
    target = Path(target).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source == target:
        raise ValueError("backup target must differ from source")
    StateDatabase(source).backup_to(target)
    verified = verify_database(target)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "backup": verified,
    }
    manifest_path = target.with_suffix(target.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**manifest, "manifest": str(manifest_path)}


def restore_database(backup: Path, target: Path) -> dict[str, Any]:
    """Atomically restore a verified backup and retain the previous target."""
    backup = Path(backup).resolve()
    target = Path(target).resolve()
    verified = verify_database(backup)
    if verified["integrity"] != "ok":
        raise RuntimeError(f"backup integrity check failed: {verified['integrity']}")
    target.parent.mkdir(parents=True, exist_ok=True)
    recovery_backup: Path | None = None
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        recovery_backup = target.with_suffix(target.suffix + f".pre-restore-{stamp}")
        backup_database(target, recovery_backup)

    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".restore", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with sqlite3.connect(backup) as source, sqlite3.connect(temporary) as destination:
            source.backup(destination)
        if verify_database(temporary)["integrity"] != "ok":
            raise RuntimeError("restored temporary database failed integrity verification")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "restored",
        "target": verify_database(target),
        "pre_restore_backup": str(recovery_backup) if recovery_backup else None,
    }


def clean_loader_cache(root: Path, *, older_than_seconds: float, dry_run: bool = True) -> dict[str, int]:
    """Inventory or remove old loader cache pairs under one explicit root."""
    if older_than_seconds < 0:
        raise ValueError("older_than_seconds must be non-negative")
    root = Path(root).resolve()
    cutoff = datetime.now(timezone.utc).timestamp() - older_than_seconds
    candidates = [path for path in root.glob("*/*.parquet") if path.is_file() and path.stat().st_mtime < cutoff]
    bytes_found = 0
    files = 0
    for parquet in candidates:
        for path in (parquet, parquet.with_suffix(parquet.suffix + ".meta.json")):
            if not path.exists():
                continue
            bytes_found += path.stat().st_size
            files += 1
            if not dry_run:
                path.unlink()
    return {"entries": len(candidates), "files": files, "bytes": bytes_found, "removed": 0 if dry_run else files}
