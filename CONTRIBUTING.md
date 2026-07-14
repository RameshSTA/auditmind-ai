# Contributing

This repository follows the engineering standards described in [`README.md`](README.md)
(architecture, layering, and tech stack) and enforced by CI. Key points:

- **Branch naming:** `type/short-description` (`feat/`, `fix/`, `refactor/`, `docs/`, `chore/`, `ci/`).
- **Commits:** Conventional Commits, scoped to the bounded context touched, body explains *why*.
- **Every PR** must pass lint (`ruff`), import boundaries (`lint-imports`), type-check
  (`mypy --strict`), and the test suite before requesting review — CI enforces this; reviewers
  should spend their time on judgment, not formatting.
- **No cross-context or cross-layer imports** (domain → infrastructure, or context A → context B's
  internals) — enforced by import-linter (`apps/api/pyproject.toml`'s `[tool.importlinter]`,
  run locally with `PYTHONPATH=src lint-imports` from `apps/api/`).
- **No placeholder code.** A function either does what it claims or doesn't exist yet; there are
  no `TODO: implement` stubs merged to `main`.

See [`README.md`](README.md) for what's built, the architecture, and the roadmap.
