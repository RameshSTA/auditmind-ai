# syntax=docker/dockerfile:1
#
# Multi-stage build: the build stage carries compilers and dev tooling; the runtime
# stage ships only the installed package and its runtime dependencies, as a non-root user.
#
# Pinned to an exact version tag (3.12.10-slim, never a floating `3.12-slim` or `latest`) rather
# than a content digest: this environment has no Docker daemon available to resolve and verify the
# real digest, and fabricating one would silently break the build or, worse, silently pull the
# wrong image. Before this ships to CI, resolve and pin the digest with:
#   docker pull python:3.12.10-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.12.10-slim
# then replace both FROM lines below with `python:3.12.10-slim@sha256:<resolved-digest>`.

FROM python:3.12.10-slim AS build

# `hdbscan` has no prebuilt wheel for this
# platform/Python combination and compiles Cython-generated C extensions at install time — a
# bare `python:*-slim` image has no compiler at all, and `pip install` fails with `gcc: No such
# file or directory` without this. Build tools stay confined to this build stage; the runtime
# stage below still ships without a compiler, matching the multi-stage split's whole purpose.
RUN apt-get update && apt-get install --no-install-recommends -y build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Only copy dependency manifests first so this layer caches independently of source changes.
COPY pyproject.toml ./
COPY src ./src

# sentence-transformers pulls in torch, whose default PyPI wheel bundles the full NVIDIA CUDA
# toolkit (~2GB of nvidia-*/triton packages) even here, where the image never runs on a GPU —
# this runtime is CPU-only (BGE-M3 in retrieval/infrastructure confirms the same). Installing the
# CPU-only wheel from PyTorch's own index first, with PYTHONPATH already pointed at --prefix's
# site-packages, means the later `pip install .` sees torch as already satisfied there and never
# re-resolves it against the default index — cuts this layer from several GB and several minutes
# down to a normal dependency install. (PYTHONPATH, not just --prefix, is what makes the second
# install see it: pip's "already satisfied" check walks sys.path, which --prefix alone doesn't
# join without also setting this.)
ENV PYTHONPATH=/install/lib/python3.12/site-packages
RUN pip install --no-cache-dir --prefix=/install torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --prefix=/install .

# ---------------------------------------------------------------------------

FROM python:3.12.10-slim AS runtime

# Non-root user (OWASP A05 — Security Misconfiguration).
RUN groupadd --gid 1000 auditmind && \
    useradd --uid 1000 --gid auditmind --shell /usr/sbin/nologin --create-home auditmind

COPY --from=build /install /usr/local

WORKDIR /app

# Migration files ship in the same image (alembic is already a main dependency in pyproject.toml)
# so `docker run <image> alembic upgrade head` works against any environment this image is
# deployed to, without a separate migration-only image to keep in sync.
COPY alembic.ini ./
COPY migrations ./migrations

# No `USER auditmind` here (contrast the non-root invariant this used to set directly): a fresh
# named volume is always created root-owned by Docker no matter what USER the image declares, so
# /data/blobs (docker-compose.yml's `blob_storage` mount) would still be unwritable by uid 1000 on
# first boot. docker-entrypoint.py runs as root, fixes that volume's ownership, then drops
# privileges to `auditmind` via exec before running CMD — the app process itself still ends up
# non-root (OWASP A05), just one step later than a bare USER instruction.
# Services that override this image's entrypoint (e.g. docker-compose.yml's `migrate`, which runs
# `alembic` directly and never touches /data/blobs) bypass this script entirely and must set
# `user:` themselves to stay non-root.
COPY docker-entrypoint.py /usr/local/bin/docker-entrypoint.py
RUN chmod +x /usr/local/bin/docker-entrypoint.py

EXPOSE 8000

# Liveness is intentionally the only thing this check verifies — readiness (which
# will check downstream dependencies once they exist) is a separate HTTP endpoint, not this probe.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)" || exit 1

ENTRYPOINT ["python3", "/usr/local/bin/docker-entrypoint.py"]
CMD ["uvicorn", "auditmind_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
