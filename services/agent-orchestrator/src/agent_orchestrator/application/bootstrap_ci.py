"""Non-parametric bootstrap confidence interval on a binary rate — the in-app counterpart to
``analytics/notebooks/hitl_agreement_analysis.ipynb``'s methodology, computed live over an
engagement's real HITL decisions rather than as a separate offline artifact.

Stdlib-only (``random``, no numpy/scipy) — this service has no other reason to carry a numeric
dependency, and resampling a few dozen booleans doesn't need one.
"""

from __future__ import annotations

import random

from agent_orchestrator.domain.entities import ApprovalRateEstimate


def bootstrap_rate_ci(
    outcomes: list[bool], *, n_boot: int = 10_000, seed: int = 42
) -> ApprovalRateEstimate | None:
    """Resamples ``outcomes`` with replacement ``n_boot`` times and returns the 2.5th/97.5th
    percentile of the resulting rate distribution — a proper non-parametric CI rather than a
    normal-approximation formula that breaks down at the small sample sizes a fresh engagement
    will typically have. Returns ``None`` for an empty sample rather than a divide-by-zero or a
    fabricated 0%."""
    if not outcomes:
        return None

    rng = random.Random(seed)
    n = len(outcomes)
    point_estimate = sum(outcomes) / n

    boot_rates = []
    for _ in range(n_boot):
        resample = rng.choices(outcomes, k=n)
        boot_rates.append(sum(resample) / n)
    boot_rates.sort()

    def percentile(p: float) -> float:
        index = min(int(p / 100.0 * len(boot_rates)), len(boot_rates) - 1)
        return boot_rates[index]

    return ApprovalRateEstimate(
        point_estimate=point_estimate,
        ci_low=percentile(2.5),
        ci_high=percentile(97.5),
        sample_size=n,
    )
