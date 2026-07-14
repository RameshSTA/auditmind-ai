"""Postgres adapters for the ``RunRepository`` and ``HitlRepository`` ports (Phase 4 §1).

Both write through SQLAlchemy the same way ``apps/api``'s owned-table repositories do — the
``agent`` schema is this service's, so it owns its writes. Every method operates within the
request's own RLS-bound session (``app.current_user_id`` set by ``set_rls_user_context``), so none
of these queries adds a ``WHERE engagement_id`` clause of its own for isolation: the RLS policy on
each ``agent.*`` table enforces it, exactly as it does for every engagement-scoped table in
``apps/api`` (Phase 4 §12). A row a caller has no membership for is invisible — a get returns
``None`` and a list omits it, the database's doing, not a filter here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.domain.agents import HitlDecision, MessageRole, MessageType, RunStatus
from agent_orchestrator.domain.entities import AgentRun, HitlInterrupt, Message
from agent_orchestrator.infrastructure.models import (
    AgentRunModel,
    HitlInterruptModel,
    MessageModel,
)


def _run_to_entity(model: AgentRunModel) -> AgentRun:
    return AgentRun(
        id=str(model.id),
        engagement_id=str(model.engagement_id),
        use_case=model.use_case,
        status=RunStatus(model.status),
        initiated_by=str(model.initiated_by),
        task=model.task,
        created_at=model.created_at,
    )


def _interrupt_to_entity(model: HitlInterruptModel) -> HitlInterrupt:
    return HitlInterrupt(
        id=str(model.id),
        run_id=str(model.run_id),
        engagement_id=str(model.engagement_id),
        step_name=model.step_name,
        decision=HitlDecision(model.decision) if model.decision is not None else None,
        reviewer_id=str(model.reviewer_id) if model.reviewer_id is not None else None,
        reason=model.reason,
        created_at=model.created_at,
        resolved_at=model.resolved_at,
    )


class PostgresRunRepository:
    """``RunRepository`` backed by ``agent.runs``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self, run: AgentRun) -> AgentRun:
        model = AgentRunModel(
            engagement_id=run.engagement_id,
            use_case=run.use_case,
            status=run.status.value,
            initiated_by=run.initiated_by,
            task=run.task,
        )
        self._session.add(model)
        await self._session.flush()
        return _run_to_entity(model)

    async def get_run(self, run_id: str) -> AgentRun | None:
        result = await self._session.execute(
            select(AgentRunModel).where(AgentRunModel.id == run_id)
        )
        model = result.scalar_one_or_none()
        return _run_to_entity(model) if model is not None else None

    async def set_status(self, run_id: str, status: RunStatus) -> None:
        result = await self._session.execute(
            select(AgentRunModel).where(AgentRunModel.id == run_id)
        )
        model = result.scalar_one_or_none()
        # A None here means RLS hid the row (or it never existed) — silently do nothing rather than
        # raise, matching the "the database decides visibility" contract the get above documents.
        if model is not None:
            model.status = status.value

    async def list_runs(self, engagement_id: str) -> list[AgentRun]:
        # No WHERE engagement_id here on purpose — RLS scopes the result to the caller's engagements
        # already. The argument is retained in the port for callers that key their own logic on it.
        result = await self._session.execute(
            select(AgentRunModel)
            .where(AgentRunModel.engagement_id == engagement_id)
            .order_by(AgentRunModel.created_at.desc())
        )
        return [_run_to_entity(m) for m in result.scalars().all()]


class PostgresHitlRepository:
    """``HitlRepository`` backed by ``agent.hitl_interrupts``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open_interrupt(self, interrupt: HitlInterrupt) -> HitlInterrupt:
        model = HitlInterruptModel(
            run_id=interrupt.run_id,
            engagement_id=interrupt.engagement_id,
            step_name=interrupt.step_name,
        )
        self._session.add(model)
        await self._session.flush()
        return _interrupt_to_entity(model)

    async def get_interrupt(self, interrupt_id: str) -> HitlInterrupt | None:
        result = await self._session.execute(
            select(HitlInterruptModel).where(HitlInterruptModel.id == interrupt_id)
        )
        model = result.scalar_one_or_none()
        return _interrupt_to_entity(model) if model is not None else None

    async def resolve(
        self, interrupt_id: str, *, decision: HitlDecision, reviewer_id: str, reason: str | None
    ) -> HitlInterrupt:
        result = await self._session.execute(
            select(HitlInterruptModel).where(HitlInterruptModel.id == interrupt_id)
        )
        model = result.scalar_one()
        model.decision = decision.value
        model.reviewer_id = reviewer_id  # type: ignore[assignment]
        model.reason = reason
        model.resolved_at = datetime.now(UTC)
        return _interrupt_to_entity(model)

    async def list_open_for_run(self, run_id: str) -> list[HitlInterrupt]:
        result = await self._session.execute(
            select(HitlInterruptModel)
            .where(HitlInterruptModel.run_id == run_id)
            .where(HitlInterruptModel.decision.is_(None))
            .order_by(HitlInterruptModel.created_at.asc())
        )
        return [_interrupt_to_entity(m) for m in result.scalars().all()]

    async def list_all_for_engagement(self, engagement_id: str) -> list[HitlInterrupt]:
        result = await self._session.execute(
            select(HitlInterruptModel)
            .where(HitlInterruptModel.engagement_id == engagement_id)
            .order_by(HitlInterruptModel.created_at.desc())
        )
        return [_interrupt_to_entity(m) for m in result.scalars().all()]


def _message_to_entity(model: MessageModel) -> Message:
    return Message(
        id=str(model.id),
        engagement_id=str(model.engagement_id),
        user_id=str(model.user_id),
        role=MessageRole(model.role),
        message_type=MessageType(model.message_type),
        content=model.content,
        run_id=str(model.run_id) if model.run_id is not None else None,
        metadata=model.extra,
        created_at=model.created_at,
    )


class PostgresMessageRepository:
    """``MessageRepository`` backed by ``agent.messages``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_message(self, message: Message) -> Message:
        model = MessageModel(
            engagement_id=message.engagement_id,
            user_id=message.user_id,
            role=message.role.value,
            message_type=message.message_type.value,
            content=message.content,
            run_id=message.run_id,
            extra=message.metadata,
        )
        self._session.add(model)
        await self._session.flush()
        return _message_to_entity(model)

    async def get_message(self, message_id: str) -> Message | None:
        result = await self._session.execute(
            select(MessageModel).where(MessageModel.id == message_id)
        )
        model = result.scalar_one_or_none()
        return _message_to_entity(model) if model is not None else None

    async def list_messages(self, *, engagement_id: str, user_id: str) -> list[Message]:
        # No WHERE engagement_id/user_id filtering for isolation beyond what RLS already enforces
        # (a private-thread policy checking BOTH membership and user_id — see the migration) —
        # these arguments narrow an already-correctly-scoped result, matching every other
        # repository's stated convention in this service.
        result = await self._session.execute(
            select(MessageModel)
            .where(MessageModel.engagement_id == engagement_id)
            .where(MessageModel.user_id == user_id)
            .order_by(MessageModel.created_at.asc())
        )
        return [_message_to_entity(m) for m in result.scalars().all()]
