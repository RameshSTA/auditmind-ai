"""Cross-validated performance metrics for the risk-scoring ensemble's ML-dependent signals
(Isolation Forest, HDBSCAN) and an ablation of the weighted combiner's fixed weights
(``combiner.py``) — the product's own in-app counterpart to a data-scientist's offline model
validation, computed live over an engagement's real data rather than left as a separate artifact.

**Ground-truth caveat, stated once here and surfaced by every field this module produces:** this
demo platform has no independently-labeled fraud ground truth. Every metric below treats the rule
engine's own flagged anomalies (``rules.py``) as a proxy positive label. A high ROC-AUC here means
the ML signals agree with the deterministic rules' notion of "anomalous" — it is evidence of
ensemble consistency, not of validated real-world fraud-detection accuracy. See
``ModelValidationResult``'s docstring for what a production-grade evaluation would still need.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from hdbscan import HDBSCAN
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_score, recall_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold

from auditmind_api.risk.application.features import TransactionFeatures

# Mirrors ml_signals.py's own floor — below this, an Isolation Forest / HDBSCAN result is
# statistically meaningless, not just this validation's cross-validation splits.
_MINIMUM_SAMPLE_SIZE = 10
_CV_FOLDS = 5
_HDBSCAN_MIN_CLUSTER_SIZE = 5
_BOOTSTRAP_RESAMPLES = 15
_PRECISION_PERCENTILE = 90.0

# Must mirror combiner.py's own `_WEIGHTS` — duplicated rather than imported because combiner.py
# treats it as a private implementation detail (leading underscore); this module needs the same
# values to ablate against, not to change how the product actually combines signals.
_COMBINER_WEIGHTS: dict[str, float] = {
    "rule_engine": 0.40,
    "isolation_forest": 0.30,
    "hdbscan_cohort": 0.15,
    "graph_centrality": 0.15,
}


@dataclass(frozen=True)
class IsolationForestValidation:
    roc_auc_mean: float
    roc_auc_std: float
    precision_at_p90_mean: float
    recall_at_p90_mean: float
    fold_count: int


@dataclass(frozen=True)
class HdbscanStabilityResult:
    noise_fraction_mean: float
    noise_fraction_std: float
    cluster_count_mean: float
    resample_count: int


@dataclass(frozen=True)
class CombinerAblationEntry:
    signal_name: str
    auc_without_signal: float
    delta: float


@dataclass(frozen=True)
class ModelValidationResult:
    """Everything the in-app "Model Validation" view needs. ``isolation_forest`` /
    ``hdbscan_stability`` / ``baseline_combined_auc`` are ``None`` when there isn't enough real
    data yet to compute them meaningfully (below ``_MINIMUM_SAMPLE_SIZE``, or a population with no
    class variation in the proxy label) — never a fabricated placeholder number.

    A production-grade validation would additionally need: investigator-confirmed labels
    (``risk.anomalies.status``'s ``true_positive``/``false_positive`` disposition is the right
    long-run source once there's enough reviewed volume), an out-of-time train/validate split
    rather than random k-fold, and a cost-sensitive operating threshold chosen with the audit team
    rather than a fixed 90th percentile.
    """

    transaction_count: int
    flagged_count: int
    isolation_forest: IsolationForestValidation | None
    hdbscan_stability: HdbscanStabilityResult | None
    baseline_combined_auc: float | None
    combiner_ablation: tuple[CombinerAblationEntry, ...]


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


def _has_class_variation(y: np.ndarray) -> bool:
    return bool(0 < y.sum() < len(y))


def cross_validate_isolation_forest(
    features: list[TransactionFeatures], labels: list[bool]
) -> IsolationForestValidation | None:
    if len(features) < _MINIMUM_SAMPLE_SIZE:
        return None
    matrix = _feature_matrix(features)
    y = np.array(labels, dtype=int)
    if not _has_class_variation(y):
        return None

    n_splits = min(_CV_FOLDS, int(y.sum()), int(len(y) - y.sum()))
    if n_splits < 2:
        return None
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)

    aucs: list[float] = []
    precisions: list[float] = []
    recalls: list[float] = []
    for train_idx, test_idx in skf.split(matrix, y):
        model = IsolationForest(contamination="auto", random_state=42)
        model.fit(matrix[train_idx])
        raw = -model.score_samples(matrix[test_idx])
        lo, hi = float(raw.min()), float(raw.max())
        scores = (raw - lo) / (hi - lo) if hi > lo else np.zeros_like(raw)
        y_test = y[test_idx]
        if not _has_class_variation(y_test):
            continue
        aucs.append(float(roc_auc_score(y_test, scores)))
        threshold = np.percentile(scores, _PRECISION_PERCENTILE)
        pred = (scores >= threshold).astype(int)
        precisions.append(float(precision_score(y_test, pred, zero_division=0)))
        recalls.append(float(recall_score(y_test, pred, zero_division=0)))

    if not aucs:
        return None
    return IsolationForestValidation(
        roc_auc_mean=float(np.mean(aucs)),
        roc_auc_std=float(np.std(aucs)),
        precision_at_p90_mean=float(np.mean(precisions)),
        recall_at_p90_mean=float(np.mean(recalls)),
        fold_count=len(aucs),
    )


def hdbscan_stability(features: list[TransactionFeatures]) -> HdbscanStabilityResult | None:
    if len(features) < _MINIMUM_SAMPLE_SIZE:
        return None
    matrix = _feature_matrix(features)
    rng = np.random.default_rng(42)
    n = len(matrix)
    noise_fractions: list[float] = []
    cluster_counts: list[int] = []
    for _ in range(_BOOTSTRAP_RESAMPLES):
        sample_idx = rng.choice(n, size=n, replace=True)
        cluster_labels = HDBSCAN(min_cluster_size=_HDBSCAN_MIN_CLUSTER_SIZE).fit_predict(
            matrix[sample_idx]
        )
        noise_fractions.append(float(np.mean(cluster_labels == -1)))
        cluster_counts.append(int(cluster_labels.max() + 1) if cluster_labels.max() >= 0 else 0)
    return HdbscanStabilityResult(
        noise_fraction_mean=float(np.mean(noise_fractions)),
        noise_fraction_std=float(np.std(noise_fractions)),
        cluster_count_mean=float(np.mean(cluster_counts)),
        resample_count=_BOOTSTRAP_RESAMPLES,
    )


def ablate_combiner(
    contributing_factors_by_transaction: dict[str, dict[str, object]],
    labels_by_transaction: dict[str, bool],
) -> tuple[float | None, tuple[CombinerAblationEntry, ...]]:
    """Recomputes the combined score with each signal's weight zeroed out (redistributed
    proportionally to the rest) and compares ROC-AUC against the real, already-persisted combined
    score — using ``risk.risk_scores.contributing_factors`` rather than recomputing signals from
    scratch, the same approach the offline notebook uses."""
    ids = [
        txn_id
        for txn_id in contributing_factors_by_transaction
        if txn_id in labels_by_transaction
    ]
    if len(ids) < _MINIMUM_SAMPLE_SIZE:
        return None, ()

    y = np.array([labels_by_transaction[i] for i in ids], dtype=int)
    if not _has_class_variation(y):
        return None, ()

    def component(txn_id: str, name: str) -> float:
        entry = contributing_factors_by_transaction[txn_id].get(name)
        if isinstance(entry, dict) and isinstance(entry.get("value"), int | float):
            return float(entry["value"])
        return float("nan")

    final_scores = np.array(
        [contributing_factors_by_transaction[i].get("final_score", 0.0) for i in ids],
        dtype=float,
    )
    try:
        baseline_auc: float | None = float(roc_auc_score(y, final_scores))
    except ValueError:
        baseline_auc = None

    entries: list[CombinerAblationEntry] = []
    for dropped in _COMBINER_WEIGHTS:
        present_signals = [
            name
            for name in _COMBINER_WEIGHTS
            if name != dropped
            and any(not np.isnan(component(i, name)) for i in ids)
        ]
        if not present_signals:
            continue
        renormalized = {
            name: _COMBINER_WEIGHTS[name] / sum(_COMBINER_WEIGHTS[p] for p in present_signals)
            for name in present_signals
        }
        ablated_scores = np.array(
            [
                sum(
                    (component(i, name) if not np.isnan(component(i, name)) else 0.0) * weight
                    for name, weight in renormalized.items()
                )
                for i in ids
            ]
        )
        try:
            ablated_auc = float(roc_auc_score(y, ablated_scores))
        except ValueError:
            continue
        delta = ablated_auc - baseline_auc if baseline_auc is not None else 0.0
        entries.append(
            CombinerAblationEntry(signal_name=dropped, auc_without_signal=ablated_auc, delta=delta)
        )

    return baseline_auc, tuple(entries)
