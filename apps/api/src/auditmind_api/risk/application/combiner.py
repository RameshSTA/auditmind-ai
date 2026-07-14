"""The weighted-linear risk combiner (Phase 7 §4) — turns the ensemble's independent signals into
one composite score with a reportable per-signal breakdown. Pure function, no I/O, the same shape
as ``rules.py``/``ml_signals.py``.

**Weighted-linear, not weighted-logistic.** Phase 7 §4 presents both as options and selects
primarily for properties they *share* — "every factor's contribution is a direct, reportable
coefficient... auditors can reweight per engagement and understand exactly what changed" — not for
the logistic curve specifically. Every signal here is already normalized to a common `[0, 100]`
scale by its own producer (``ml_signals.py``'s min-max normalization, the rule-engine severity
mapping below, the graph-centrality scaling below), so a weighted arithmetic mean already *is* a
linear combiner satisfying that selection reasoning, without introducing an arbitrary logistic
steepness parameter this codebase has no principled way to choose.

**Per-engagement reweighting (Phase 1 FR-4.2) is not implemented — the weights below are fixed
constants.** A genuine reweighting UI/API needs a place to store an engagement's chosen weights
(a new column or table) and a decision about who's allowed to change them; out of scope for this
increment, see the increment doc.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from auditmind_api.risk.domain.entities import AnomalySeverity

_WEIGHTS: dict[str, float] = {
    "rule_engine": 0.40,
    "isolation_forest": 0.30,
    "hdbscan_cohort": 0.15,
    "graph_centrality": 0.15,
}

_SEVERITY_SCORES: dict[AnomalySeverity, float] = {
    AnomalySeverity.LOW: 25.0,
    AnomalySeverity.MEDIUM: 50.0,
    AnomalySeverity.HIGH: 75.0,
    AnomalySeverity.CRITICAL: 100.0,
}

# A vendor with this many (or more) transactions in the engagement is treated as "established" —
# contributing nothing extra to risk. Below that, risk scales up toward a brand-new, one-off
# vendor — the same "new vendor, low tenure" risk factor Phase 7 §5's own waterfall example (Fig.
# 2) shows as a *positive* (risk-increasing) contributor, not an arbitrary interpretation invented
# for this codebase.
_CENTRALITY_ESTABLISHED_THRESHOLD = 10


@dataclass(frozen=True)
class CombinedScore:
    score: Decimal
    contributing_factors: dict[str, Any]


def _graph_centrality_score(vendor_transaction_count: int) -> float:
    capped = min(vendor_transaction_count, _CENTRALITY_ESTABLISHED_THRESHOLD)
    return 100.0 * (1 - capped / _CENTRALITY_ESTABLISHED_THRESHOLD)


def combine_signals(
    *,
    rule_engine_max_severity: AnomalySeverity | None,
    isolation_forest_score: float | None,
    hdbscan_is_noise: bool | None,
    vendor_transaction_count: int | None,
) -> CombinedScore:
    """Each signal is optional — a transaction with no vendor name has no graph-centrality signal,
    an engagement below the ML minimum sample size has no Isolation Forest/HDBSCAN signal, and a
    transaction with no open rule-engine anomaly has no rule-engine signal. Missing signals are
    excluded entirely, not treated as zero — a zero would silently claim "definitely not risky by
    this measure," which is a different (and false) statement from "this measure wasn't computed."
    The present signals' weights are renormalized so they still sum to the full score range,
    rather than the final score shrinking just because fewer signals happened to be available.
    """
    components: dict[str, float] = {}
    if rule_engine_max_severity is not None:
        components["rule_engine"] = _SEVERITY_SCORES[rule_engine_max_severity]
    if isolation_forest_score is not None:
        components["isolation_forest"] = isolation_forest_score
    if hdbscan_is_noise is not None:
        components["hdbscan_cohort"] = 100.0 if hdbscan_is_noise else 0.0
    if vendor_transaction_count is not None:
        components["graph_centrality"] = _graph_centrality_score(vendor_transaction_count)

    if not components:
        return CombinedScore(score=Decimal("0.00"), contributing_factors={})

    total_weight = sum(_WEIGHTS[name] for name in components)
    weighted_sum = sum(_WEIGHTS[name] * value for name, value in components.items())
    final_score = weighted_sum / total_weight

    contributing_factors: dict[str, Any] = {
        name: {"value": round(value, 2), "weight": _WEIGHTS[name]}
        for name, value in components.items()
    }
    contributing_factors["final_score"] = round(final_score, 2)

    return CombinedScore(
        score=Decimal(str(final_score)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        contributing_factors=contributing_factors,
    )
