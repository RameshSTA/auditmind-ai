# syntax=docker/dockerfile:1
#
# Multi-stage build (Phase 12 §1), mirroring apps/api/Dockerfile's structure. Unlike apps/api
# (which needs build-essential for hdbscan's C extensions, Increment 10), this service's
# dependencies (langgraph, litellm, fastapi, sqlalchemy, ...) all ship prebuilt wheels — the build
# stage stays compiler-free, same as the runtime stage.
#
# Pinned to an exact version tag, not a floating tag or an unresolved digest — see
# apps/api/Dockerfile's identical comment for why the digest isn't pinned in this environment (no
# Docker daemon available to resolve and verify one) and the exact command to run before this
# image ships anywhere real.

FROM python:3.12.10-slim AS build

WORKDIR /build

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir --prefix=/install .

# ---------------------------------------------------------------------------

FROM python:3.12.10-slim AS runtime

# Non-root user (Phase 12 §1, Phase 11 OWASP A05 — Security Misconfiguration).
RUN groupadd --gid 1000 auditmind && \
    useradd --uid 1000 --gid auditmind --shell /usr/sbin/nologin --create-home auditmind

COPY --from=build /install /usr/local

WORKDIR /app

# Migration files ship in the same image (alembic is already a main dependency) so
# `docker run <image> alembic upgrade head` works against any environment this image is deployed
# to — the same self-contained-migration convention apps/api/Dockerfile uses (Phase 12 §7).
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config

USER auditmind

EXPOSE 8000

# Liveness only (Phase 12 §13) — readiness is the separate /readyz endpoint, not this probe.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)" || exit 1

CMD ["uvicorn", "agent_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
