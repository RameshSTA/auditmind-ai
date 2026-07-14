"""Tests for EvaluationService — aggregation over fake RunRepository/HitlRepository, no
database, no network."""

from __future__ import annotations

from agent_orchestrator.application.evaluation import EvaluationService
from agent_orchestrator.domain.agents import HitlDecision, RunStatus
from agent_orchestrator.domain.entities import AgentRun, HitlInterrupt


class FakeRunRepository:
    def __init__(self, runs: list[AgentRun] | None = None) -> None:
        self._runs = runs or []

    async def create_run(self, run: AgentRun) -> AgentRun:
        raise NotImplementedError

    async def get_run(self, run_id: str) -> AgentRun | None:
        raise NotImplementedError

    async def set_status(self, run_id: str, status: RunStatus) -> None:
        raise NotImplementedError

    async def list_runs(self, engagement_id: str) -> list[AgentRun]:
        return [r for r in self._runs if r.engagement_id == engagement_id]


class FakeHitlRepository:
    def __init__(self, interrupts: list[HitlInterrupt] | None = None) -> None:
        self._interrupts = interrupts or []

    async def open_interrupt(self, interrupt: HitlInterrupt) -> HitlInterrupt:
        raise NotImplementedError

    async def get_interrupt(self, interrupt_id: str) -> HitlInterrupt | None:
        raise NotImplementedError

    async def resolve(
        self, interrupt_id: str, *, decision: HitlDecision, reviewer_id: str, reason: str | None
    ) -> HitlInterrupt:
        raise NotImplementedError

    async def list_open_for_run(self, run_id: str) -> list[HitlInterrupt]:
        raise NotImplementedError

    async def list_all_for_engagement(self, engagement_id: str) -> list[HitlInterrupt]:
        return [i for i in self._interrupts if i.engagement_id == engagement_id]


def _run(engagement_id: str, use_case: str, status: RunStatus) -> AgentRun:
    return AgentRun(
        id=f"run-{use_case}-{status.value}",
        engagement_id=engagement_id,
        use_case=use_case,
        status=status,
        initiated_by="user-1",
        task="task",
    )


def _interrupt(
    engagement_id: str,
    run_id: str,
    decision: HitlDecision | None,
    reason: str | None = None,
) -> HitlInterrupt:
    return HitlInterrupt(
        id=f"interrupt-{run_id}-{decision.value if decision else 'open'}",
        run_id=run_id,
        engagement_id=engagement_id,
        step_name="hitl",
        decision=decision,
        reason=reason,
    )


async def test_compute_metrics_with_no_runs_returns_all_zeros() -> None:
    service = EvaluationService(FakeRunRepository(), FakeHitlRepository())

    metrics = await service.compute_metrics("eng-1")

    assert metrics.total_runs == 0
    assert metrics.run_status_counts == {}
    assert metrics.use_case_breakdown == ()
    assert metrics.total_interrupts == 0
    assert metrics.open_interrupts == 0
    assert metrics.resolved_interrupts == 0
    assert metrics.decision_counts == {}
    assert metrics.recent_reject_edit_reasons == ()
    assert metrics.approval_rate is None


async def test_compute_metrics_only_counts_runs_for_the_given_engagement() -> None:
    runs = [
        _run("eng-1", "control_test", RunStatus.COMPLETED),
        _run("eng-2", "control_test", RunStatus.FAILED),
    ]
    service = EvaluationService(FakeRunRepository(runs), FakeHitlRepository())

    metrics = await service.compute_metrics("eng-1")

    assert metrics.total_runs == 1
    assert metrics.run_status_counts == {"completed": 1}


async def test_compute_metrics_breaks_down_run_outcomes_by_use_case() -> None:
    runs = [
        _run("eng-1", "control_test", RunStatus.COMPLETED),
        _run("eng-1", "control_test", RunStatus.CANNOT_CONCLUDE),
        _run("eng-1", "fraud_triage", RunStatus.HALTED),
    ]
    service = EvaluationService(FakeRunRepository(runs), FakeHitlRepository())

    metrics = await service.compute_metrics("eng-1")

    breakdown = {row.use_case: row.status_counts for row in metrics.use_case_breakdown}
    assert breakdown == {
        "control_test": {"completed": 1, "cannot_conclude": 1},
        "fraud_triage": {"halted": 1},
    }


async def test_compute_metrics_counts_open_vs_resolved_interrupts_and_decisions() -> None:
    interrupts = [
        _interrupt("eng-1", "run-1", decision=None),
        _interrupt("eng-1", "run-2", decision=HitlDecision.APPROVE),
        _interrupt("eng-1", "run-3", decision=HitlDecision.REJECT, reason="Missing evidence."),
    ]
    service = EvaluationService(FakeRunRepository(), FakeHitlRepository(interrupts))

    metrics = await service.compute_metrics("eng-1")

    assert metrics.total_interrupts == 3
    assert metrics.open_interrupts == 1
    assert metrics.resolved_interrupts == 2
    assert metrics.decision_counts == {"approve": 1, "reject": 1}


async def test_compute_metrics_only_surfaces_reject_and_edit_reasons_not_approve() -> None:
    interrupts = [
        _interrupt("eng-1", "run-1", decision=HitlDecision.APPROVE, reason=None),
        _interrupt("eng-1", "run-2", decision=HitlDecision.REJECT, reason="Missing evidence."),
        _interrupt("eng-1", "run-3", decision=HitlDecision.EDIT, reason="Tone too strong."),
    ]
    service = EvaluationService(FakeRunRepository(), FakeHitlRepository(interrupts))

    metrics = await service.compute_metrics("eng-1")

    reasons = {i.reason for i in metrics.recent_reject_edit_reasons}
    assert reasons == {"Missing evidence.", "Tone too strong."}


async def test_compute_metrics_ignores_interrupts_from_a_different_engagement() -> None:
    interrupts = [_interrupt("eng-2", "run-1", decision=HitlDecision.REJECT, reason="x")]
    service = EvaluationService(FakeRunRepository(), FakeHitlRepository(interrupts))

    metrics = await service.compute_metrics("eng-1")

    assert metrics.total_interrupts == 0


async def test_compute_metrics_bootstraps_a_confidence_interval_on_the_approval_rate() -> None:
    interrupts = [
        _interrupt("eng-1", "run-1", decision=HitlDecision.APPROVE),
        _interrupt("eng-1", "run-2", decision=HitlDecision.APPROVE),
        _interrupt("eng-1", "run-3", decision=HitlDecision.REJECT, reason="Missing evidence."),
    ]
    service = EvaluationService(FakeRunRepository(), FakeHitlRepository(interrupts))

    metrics = await service.compute_metrics("eng-1")

    assert metrics.approval_rate is not None
    assert metrics.approval_rate.sample_size == 3
    assert metrics.approval_rate.point_estimate == 2 / 3
    assert metrics.approval_rate.ci_low <= metrics.approval_rate.point_estimate
    assert metrics.approval_rate.point_estimate <= metrics.approval_rate.ci_high


async def test_compute_metrics_estimates_no_approval_rate_with_no_resolved_interrupts() -> None:
    interrupts = [_interrupt("eng-1", "run-1", decision=None)]
    service = EvaluationService(FakeRunRepository(), FakeHitlRepository(interrupts))

    metrics = await service.compute_metrics("eng-1")

    assert metrics.approval_rate is None
