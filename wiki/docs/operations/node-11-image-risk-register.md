# Node 11 image vulnerability risk register

Status: **resolved; strict gate passing without exceptions**

- Scan date: 2026-07-18
- Image: `vibe-trading:node11` (`sha256:820e58d4a226b780d75b36b690b02bc86da4dd2ad5de62b0ca0673974601bd47`)
- Base: Ubuntu 24.04, pinned manifest digest
- Scanner: Trivy 0.69.3 safe immutable release
- Policy: `CRITICAL,HIGH`, `ignore-unfixed=false`

## Current result

- Ubuntu 24.04 OS packages: 0 Critical and 0 High findings.
- Python packages: 0 Critical and 0 High findings.
- The strict scanner exits successfully; no ignore file, blanket `ignore-unfixed`, or CVE exception is present.
- The production image passes non-root, cold-start, readiness, protected metrics and SIGTERM smoke checks in 20 seconds.

## Resolution

The earlier Debian runtime had 37 package-level findings across 26 unique CVEs, with no vendor-fixed versions. A pinned Debian 12 Bookworm experiment was worse at 7 Critical / 33 High. Instead of accepting those findings, the production and sandbox builder/runtime ABI was unified on pinned Ubuntu 24.04 with Ubuntu Python 3.12. The locked Python environment is built on that same ABI, avoiding cross-distribution OpenSSL and SQLite linkage.

## Ongoing policy

CI and release workflows continue to fail on any Critical or High finding. Do not add a blanket `ignore-unfixed`. If a future exception is unavoidable, it requires an explicit user decision and must list every accepted CVE, image digest, rationale, compensating controls, owner and an expiry of at most 30 days. Re-scan whenever the pinned base digest or vulnerability database changes.
