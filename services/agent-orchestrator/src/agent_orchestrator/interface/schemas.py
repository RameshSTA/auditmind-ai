"""Request/response bodies for this service's routes (Phase 3 §1) — the only Pydantic models in
this codebase used for JSON payloads rather than the RFC 7807 error envelope, kept in the interface
layer alongside every other FastAPI-facing concern, the same convention ``apps/api`` uses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agent_orchestrator.domain.agents import HitlDecision, MessageRole, MessageType, RunStatus
from agent_orchestrator.domain.entities import (
    AgentRun,
    ApprovalRateEstimate,
    EvaluationMetrics,
    HitlInterrupt,
    Message,
    UseCaseOutcomeCounts,
)


class StartRunRequest(BaseModel):
    use_case: str = Field(min_length=1, description='e.g. "control_test", "fraud_triage".')
    task: str = Field(min_length=1, description="The natural-language task for the Planner.")


class ResolveHitlRequest(BaseModel):
    decision: HitlDecision
    reason: str | None = Field(
        default=None,
        description="Required for a reject/edit decision — the documented rationale that becomes "
        "the durable record of why a run's draft was not published (Phase 5 §15).",
    )


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, description="The auditor's chat message to AI Copilot.")


class ResolveCheckpointRequest(BaseModel):
    decision: HitlDecision
    reason: str | None = Field(
        default=None,
        description="Required for a reject/edit decision, same as the standalone HITL resolve "
        "endpoint this wraps.",
    )


class RunResponse(BaseModel):
    id: str
    engagement_id: str
    use_case: str
    status: RunStatus
    initiated_by: str
    task: str
    created_at: datetime | None

    @classmethod
    def from_entity(cls, run: AgentRun) -> RunResponse:
        return cls(
            id=run.id,
            engagement_id=run.engagement_id,
            use_case=run.use_case,
            status=run.status,
            initiated_by=run.initiated_by,
            task=run.task,
            created_at=run.created_at,
        )


class HitlInterruptResponse(BaseModel):
    id: str
    run_id: str
    engagement_id: str
    step_name: str
    decision: HitlDecision | None
    reviewer_id: str | None
    reason: str | None
    created_at: datetime | None
    resolved_at: datetime | None

    @classmethod
    def from_entity(cls, interrupt: HitlInterrupt) -> HitlInterruptResponse:
        return cls(
            id=interrupt.id,
            run_id=interrupt.run_id,
            engagement_id=interrupt.engagement_id,
            step_name=interrupt.step_name,
            decision=interrupt.decision,
            reviewer_id=interrupt.reviewer_id,
            reason=interrupt.reason,
            created_at=interrupt.created_at,
            resolved_at=interrupt.resolved_at,
        )


class UseCaseOutcomeCountsResponse(BaseModel):
    use_case: str
    status_counts: dict[str, int]

    @classmethod
    def from_entity(cls, counts: UseCaseOutcomeCounts) -> UseCaseOutcomeCountsResponse:
        return cls(use_case=counts.use_case, status_counts=counts.status_counts)


class ApprovalRateEstimateResponse(BaseModel):
    point_estimate: float
    ci_low: float
    ci_high: float
    sample_size: int

    @classmethod
    def from_entity(cls, estimate: ApprovalRateEstimate) -> ApprovalRateEstimateResponse:
        return cls(
            point_estimate=estimate.point_estimate,
            ci_low=estimate.ci_low,
            ci_high=estimate.ci_high,
            sample_size=estimate.sample_size,
        )


class MessageResponse(BaseModel):
    id: str
    engagement_id: str
    user_id: str
    role: MessageRole
    message_type: MessageType
    content: str
    run_id: str | None
    metadata: dict[str, object] | None
    created_at: datetime | None

    @classmethod
    def from_entity(cls, message: Message) -> MessageResponse:
        return cls(
            id=message.id,
            engagement_id=message.engagement_id,
            user_id=message.user_id,
            role=message.role,
            message_type=message.message_type,
            content=message.content,
            run_id=message.run_id,
            metadata=message.metadata,
            created_at=message.created_at,
        )


class EvaluationMetricsResponse(BaseModel):
    total_runs: int
    run_status_counts: dict[str, int]
    use_case_breakdown: list[UseCaseOutcomeCountsResponse]
    total_interrupts: int
    open_interrupts: int
    resolved_interrupts: int
    decision_counts: dict[str, int]
    recent_reject_edit_reasons: list[HitlInterruptResponse]
    approval_rate: ApprovalRateEstimateResponse | None

    @classmethod
    def from_entity(cls, metrics: EvaluationMetrics) -> EvaluationMetricsResponse:
        return cls(
            total_runs=metrics.total_runs,
            run_status_counts=metrics.run_status_counts,
            use_case_breakdown=[
                UseCaseOutcomeCountsResponse.from_entity(c) for c in metrics.use_case_breakdown
            ],
            total_interrupts=metrics.total_interrupts,
            open_interrupts=metrics.open_interrupts,
            resolved_interrupts=metrics.resolved_interrupts,
            decision_counts=metrics.decision_counts,
            recent_reject_edit_reasons=[
                HitlInterruptResponse.from_entity(i) for i in metrics.recent_reject_edit_reasons
            ],
            approval_rate=(
                ApprovalRateEstimateResponse.from_entity(metrics.approval_rate)
                if metrics.approval_rate
                else None
            ),
        )
