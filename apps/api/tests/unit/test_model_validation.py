"""Unit tests for risk/application/model_validation.py — the in-app counterpart to
analytics/notebooks/risk_model_validation.ipynb. Pure functions, synthetic feature matrices, no
database."""

from __future__ import annotations

from auditmind_api.risk.application.features import TransactionFeatures
from auditmind_api.risk.application.model_validation import (
    ablate_combiner,
    cross_validate_isolation_forest,
    hdbscan_stability,
)


def _clustered_features(count: int) -> list[TransactionFeatures]:
    """A population with genuine internal structure: most transactions cluster tightly near zero,
    a clear minority are pushed far out — exactly what Isolation Forest / HDBSCAN should separate
    cleanly, giving a real (not degenerate) result to assert against."""
    features = []
    for i in range(count):
        is_outlier = i % 5 == 0
        features.append(
            TransactionFeatures(
                transaction_id=f"t{i}",
                normalized_amount=0.95 if is_outlier else 0.1 + (i % 3) * 0.01,
                is_round_dollar=1.0 if is_outlier else 0.0,
                is_near_threshold=0.0,
                day_of_month_fraction=0.5,
                deviation_from_vendor_average=0.9 if is_outlier else 0.05,
            )
        )
    return features


def test_cross_validate_isolation_forest_returns_none_below_minimum_sample_size() -> None:
    features = _clustered_features(5)
    labels = [i % 5 == 0 for i in range(5)]

    result = cross_validate_isolation_forest(features, labels)

    assert result is None


def test_cross_validate_isolation_forest_separates_a_clearly_clustered_population() -> None:
    features = _clustered_features(40)
    labels = [i % 5 == 0 for i in range(40)]

    result = cross_validate_isolation_forest(features, labels)

    assert result is not None
    # A population with genuinely separable outliers should score well above chance (0.5).
    assert result.roc_auc_mean > 0.7
    assert result.fold_count > 0


def test_cross_validate_isolation_forest_returns_none_with_no_class_variation() -> None:
    features = _clustered_features(20)
    labels = [False] * 20  # nothing flagged — no proxy positives to validate against

    result = cross_validate_isolation_forest(features, labels)

    assert result is None


def test_hdbscan_stability_returns_none_below_minimum_sample_size() -> None:
    assert hdbscan_stability(_clustered_features(5)) is None


def test_hdbscan_stability_reports_a_bounded_noise_fraction() -> None:
    result = hdbscan_stability(_clustered_features(40))

    assert result is not None
    assert 0.0 <= result.noise_fraction_mean <= 1.0
    assert result.resample_count > 0


def test_ablate_combiner_returns_none_below_minimum_sample_size() -> None:
    factors: dict[str, dict[str, object]] = {f"t{i}": {"final_score": 50.0} for i in range(5)}
    labels = {f"t{i}": i % 2 == 0 for i in range(5)}

    baseline, ablation = ablate_combiner(factors, labels)

    assert baseline is None
    assert ablation == ()


def test_ablate_combiner_scores_a_population_where_the_signal_tracks_the_label() -> None:
    factors: dict[str, dict[str, object]] = {}
    labels: dict[str, bool] = {}
    for i in range(30):
        flagged = i % 3 == 0
        factors[f"t{i}"] = {
            "final_score": 90.0 if flagged else 10.0,
            "rule_engine": {"value": 90.0 if flagged else 10.0, "weight": 0.4},
            "isolation_forest": {"value": 80.0 if flagged else 20.0, "weight": 0.3},
        }
        labels[f"t{i}"] = flagged

    baseline, ablation = ablate_combiner(factors, labels)

    assert baseline is not None
    assert baseline > 0.9  # final_score tracks the label almost perfectly by construction
    assert len(ablation) > 0
    assert {entry.signal_name for entry in ablation} <= {
        "rule_engine",
        "isolation_forest",
        "hdbscan_cohort",
        "graph_centrality",
    }
