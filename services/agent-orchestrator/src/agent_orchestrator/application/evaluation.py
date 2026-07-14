"""Evaluation — real analytics over already-recorded agent runs and HITL decisions.

Deliberately not a golden-dataset grading harness: this service has no labeled answer key to score
runs against, and inventing one would mean grading against fabricated expected answers. What *is*
real and already sitting in ``agent.runs`` / ``agent.hitl_interrupts`` is exactly the data a human
reviewing this platform's own agent behavior would want: how often runs complete vs. stall vs. get
halted, broken down by use case, and how often a human reviewer approves vs. rejects vs. edits an
agent's draft. This module aggregates that, nothing more.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime

from agent_orchestrator.application.bootstrap_ci import bootstrap_rate_ci
from agent_orchestrator.domain.agents import HitlDecision
from agent_orchestrator.domain.entities import EvaluationMetrics, UseCaseOutcomeCounts
from agent_orchestrator.domain.ports import HitlRepository, RunRepository

_EPOCH = datetime.fromtimestamp(0, tz=UTC)

_RECENT_REASONS_LIMIT = 10


class EvaluationService:
    def __init__(self, run_repository: RunRepository, hitl_repository: HitlRepository) -> None:
        self._runs = run_repository
        self._hitl = hitl_repository

    async def compute_metrics(self, engagement_id: str) -> EvaluationMetrics:
        runs = await self._runs.list_runs(engagement_id)
        interrupts = await self._hitl.list_all_for_engagement(engagement_id)

        run_status_counts: Counter[str] = Counter(run.status.value for run in runs)

        status_counts_by_use_case: dict[str, Counter[str]] = defaultdict(Counter)
        for run in runs:
            status_counts_by_use_case[run.use_case][run.status.value] += 1
        use_case_breakdown = tuple(
            UseCaseOutcomeCounts(use_case=use_case, status_counts=dict(counts))
            for use_case, counts in sorted(status_counts_by_use_case.items())
        )

        resolved = [i for i in interrupts if i.decision is not None]
        decision_counts: Counter[str] = Counter(i.decision.value for i in resolved if i.decision)

        reject_edit_with_reason = [
            i
            for i in resolved
            if i.decision and i.decision.value in ("reject", "edit") and i.reason
        ]
        reject_edit_with_reason.sort(
            key=lambda i: i.resolved_at or i.created_at or _EPOCH, reverse=True
        )
        recent_reject_edit_reasons = tuple(reject_edit_with_reason[:_RECENT_REASONS_LIMIT])

        approval_outcomes = [i.decision == HitlDecision.APPROVE for i in resolved]
        approval_rate = bootstrap_rate_ci(approval_outcomes)

        return EvaluationMetrics(
            total_runs=len(runs),
            run_status_counts=dict(run_status_counts),
            use_case_breakdown=use_case_breakdown,
            total_interrupts=len(interrupts),
            open_interrupts=len(interrupts) - len(resolved),
            resolved_interrupts=len(resolved),
            decision_counts=dict(decision_counts),
            recent_reject_edit_reasons=recent_reject_edit_reasons,
            approval_rate=approval_rate,
        )
