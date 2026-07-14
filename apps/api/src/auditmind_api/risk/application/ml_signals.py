"""Multivariate outlier scoring (Isolation Forest) and cohort clustering (HDBSCAN) — the two
ML-dependent tiers of the fraud-scoring ensemble, left unbuilt until `scikit-learn`/`hdbscan`
were added.

Pure functions over a feature matrix (``features.py``'s ``TransactionFeatures``), no I/O — model
*fitting* is CPU computation over data already in hand, not a call to an external system, so this
follows the same "pure, trivially unit-testable" shape ``rules.py`` established for the non-ML
tier, rather than needing a port/adapter split the way a genuinely I/O-bound dependency (BGE-M3's
model *download*, Neo4j's network round-trip) would.
"""

from __future__ import annotations

import numpy as np
from hdbscan import HDBSCAN
from sklearn.ensemble import IsolationForest

from auditmind_api.risk.application.features import TransactionFeatures

# Isolation Forest and HDBSCAN both technically run on any sample size, but their output is
# statistically meaningless below a certain population — a "cohort" of 3 transactions isn't a
# cohort. This mirrors the same "documented floor, not forensic rigor at n=1" reasoning
# `rules.py`'s own `_BENFORD_MINIMUM_SAMPLE_SIZE` already establishes for a different technique.
_MINIMUM_SAMPLE_SIZE = 10

_HDBSCAN_MIN_CLUSTER_SIZE = 5


def _feature_matrix(features: list[TransactionFeatures]) -> np.ndarray:
    return np.array(
        [
            [
                f.normalized_amount,
                f.is_round_dollar,
                f.is_near_threshold,
                f.day_of_month_fraction,
                f.deviation_from_vendor_average,
            ]
            for f in features
        ]
    )


def compute_isolation_forest_scores(features: list[TransactionFeatures]) -> dict[str, float]:
    """Returns ``transaction_id -> anomaly score`` scaled to ``[0, 100]`` (higher = more
    anomalous). Empty below the minimum sample size, the same "nothing to report yet" behavior
    ``detect_benford_deviation`` has for an undersized population, rather than a misleadingly
    confident score computed from too little data.

    ``random_state`` is fixed — Isolation Forest's tree construction is randomized, and a score
    that changed between two calls over the *same* transaction population purely from re-rolling
    randomness would undermine "recomputing refreshes the score" (the migration's own reasoning
    for why re-scoring the same subject/version updates in place): a caller re-running this
    against unchanged data should get an unchanged score.
    """
    if len(features) < _MINIMUM_SAMPLE_SIZE:
        return {}

    matrix = _feature_matrix(features)
    model = IsolationForest(contamination="auto", random_state=42)
    model.fit(matrix)
    # `score_samples` is higher for *normal* points and lower (more negative) for outliers —
    # negated so this function's own output convention (higher = more anomalous) matches every
    # other signal in the ensemble, not sklearn's.
    raw_scores = -model.score_samples(matrix)

    minimum, maximum = float(raw_scores.min()), float(raw_scores.max())
    spread = maximum - minimum
    return {
        f.transaction_id: (
            100.0 * (float(raw_scores[i]) - minimum) / spread if spread > 0 else 0.0
        )
        for i, f in enumerate(features)
    }


def compute_hdbscan_noise_flags(features: list[TransactionFeatures]) -> dict[str, bool]:
    """Returns ``transaction_id -> is_noise``. A "noise" point (HDBSCAN's cluster label ``-1``) is
    a transaction that doesn't fit any cohort of at least ``_HDBSCAN_MIN_CLUSTER_SIZE`` similar
    transactions — the selection reasoning for HDBSCAN over K-Means specifically: it does not
    force every transaction into a cluster regardless of fit. Empty below the minimum sample size,
    same reasoning as the Isolation Forest gate above.

    **Everything coming back as noise is a legitimate result, not a bug — confirmed empirically
    while building this, worth stating explicitly so the next person who sees an all-noise result
    doesn't assume the integration is broken.** HDBSCAN's default cluster-selection method
    (excess-of-mass) only reports a region as a cluster if it's *stable relative to some
    alternative* — a population with no genuine sub-structure (every transaction independently
    random, or too few transactions for density estimation to find structure at all) has nothing
    to be stable relative to, and correctly comes back 100% noise. Verified directly: a clean,
    well-separated synthetic population (~200 points, 5 injected outliers) correctly isolates
    exactly the outliers; the same population size with no real internal structure (e.g. every
    feature independently random) correctly comes back entirely noise.
    """
    if len(features) < _MINIMUM_SAMPLE_SIZE:
        return {}

    matrix = _feature_matrix(features)
    clusterer = HDBSCAN(min_cluster_size=_HDBSCAN_MIN_CLUSTER_SIZE)
    labels = clusterer.fit_predict(matrix)
    return {f.transaction_id: bool(labels[i] == -1) for i, f in enumerate(features)}
