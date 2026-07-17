"""Strict identifier, filesystem-root, and outbound URL boundaries."""

from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


_ID_PATTERNS = {
    "run_id": re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"),
    "session_id": re.compile(r"^[a-f0-9]{12}$"),
    "swarm_run_id": re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"),
    "job_id": re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"),
    "model_id": re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"),
    "chain_id": re.compile(r"^[a-f0-9]{12}$"),
    "artifact_id": re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"),
    "strategy_id": re.compile(r"^[a-z][a-z0-9_]{0,47}$"),
}
_FALLBACK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def validate_identifier(value: str, kind: str) -> str:
    pattern = _ID_PATTERNS.get(kind, _FALLBACK_ID_RE)
    if not pattern.fullmatch(value or ""):
        raise ValueError(f"invalid {kind}")
    return value


def resolve_within_root(root: Path, identifier: str, *, kind: str) -> Path:
    """Resolve one identifier under root, rejecting traversal and symlink escape."""
    validate_identifier(identifier, kind)
    base = root.expanduser().resolve()
    candidate = (base / identifier).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"invalid {kind}: path escapes storage root") from exc
    return candidate


def _reject_non_public_ip(ip: ipaddress._BaseAddress) -> None:
    if not ip.is_global:
        raise ValueError("URL target must resolve only to public addresses")


def validate_outbound_url(
    raw: str,
    *,
    allow_loopback: bool = False,
    resolve_dns: bool = True,
) -> str:
    """Validate an HTTP(S) URL against SSRF and credential-leak primitives."""
    try:
        parsed = urlsplit(raw.strip())
    except ValueError as exc:
        raise ValueError("Invalid provider base URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Provider base URL must use http or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Provider base URL cannot contain credentials, query, or fragment")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        if not allow_loopback:
            raise ValueError("Provider base URL cannot target a local address")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        if not (allow_loopback and literal.is_loopback):
            _reject_non_public_ip(literal)
    elif resolve_dns:
        try:
            records = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("Provider base URL hostname could not be resolved") from exc
        addresses = {record[4][0].split("%", 1)[0] for record in records}
        if not addresses:
            raise ValueError("Provider base URL hostname returned no addresses")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not (allow_loopback and ip.is_loopback):
                _reject_non_public_ip(ip)

    if parsed.scheme != "https" and not allow_loopback:
        raise ValueError("Remote provider base URLs must use https")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
