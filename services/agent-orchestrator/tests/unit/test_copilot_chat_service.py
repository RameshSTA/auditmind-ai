"""Tests for CopilotChatService — the chat router (answer / action / investigate) over a real
compiled investigation graph (fakes underneath, no network/database), mirroring
test_orchestrator.py's own fixture construction so an "investigate" hand-off is genuinely
exercised end-to-end, not stubbed out."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent_orchestrator.application.copilot_chat_service import CopilotAction, CopilotChatService
from agent_orchestrator.application.orchestrator import OrchestrationService
from agent_orchestrator.domain.agents import HitlDecision, MessageRole, MessageType, RunStatus
from agent_orchestrator.domain.entities import AgentRun, HitlInterrupt, Message
from agent_orchestrator.shared.errors import NotFoundError, ValidationError
from tests.conftest import FakeApiClient, FakeLlmClient


class FakeRunRepository:
    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}
        self._next_id = 1

    async def create_run(self, run: AgentRun) -> AgentRun:
        run_id = f"run-{self._next_id}"
        self._next_id += 1
        stored = AgentRun(
            id=run_id,
            engagement_id=run.engagement_id,
            use_case=run.use_case,
            status=run.status,
            initiated_by=run.initiated_by,
            task=run.task,
        )
        self._runs[run_id] = stored
        return stored

    async def get_run(self, run_id: str) -> AgentRun | None:
        return self._runs.get(run_id)

    async def set_status(self, run_id: str, status: RunStatus) -> None:
        run = self._runs[run_id]
        self._runs[run_id] = AgentRun(
            id=run.id,
            engagement_id=run.engagement_id,
            use_case=run.use_case,
            status=status,
            initiated_by=run.initiated_by,
            task=run.task,
            created_at=run.created_at,
        )

    async def list_runs(self, engagement_id: str) -> list[AgentRun]:
        return [r for r in self._runs.values() if r.engagement_id == engagement_id]


class FakeHitlRepository:
    def __init__(self) -> None:
        self._interrupts: dict[str, HitlInterrupt] = {}
        self._next_id = 1

    async def open_interrupt(self, interrupt: HitlInterrupt) -> HitlInterrupt:
        interrupt_id = f"interrupt-{self._next_id}"
        self._next_id += 1
        stored = HitlInterrupt(
            id=interrupt_id,
            run_id=interrupt.run_id,
            engagement_id=interrupt.engagement_id,
            step_name=interrupt.step_name,
        )
        self._interrupts[interrupt_id] = stored
        return stored

    async def get_interrupt(self, interrupt_id: str) -> HitlInterrupt | None:
        return self._interrupts.get(interrupt_id)

    async def resolve(
        self, interrupt_id: str, *, decision: HitlDecision, reviewer_id: str, reason: str | None
    ) -> HitlInterrupt:
        interrupt = self._interrupts[interrupt_id]
        resolved = HitlInterrupt(
            id=interrupt.id,
            run_id=interrupt.run_id,
            engagement_id=interrupt.engagement_id,
            step_name=interrupt.step_name,
            decision=decision,
            reviewer_id=reviewer_id,
            reason=reason,
        )
        self._interrupts[interrupt_id] = resolved
        return resolved

    async def list_open_for_run(self, run_id: str) -> list[HitlInterrupt]:
        return [i for i in self._interrupts.values() if i.run_id == run_id and i.decision is None]

    async def list_all_for_engagement(self, engagement_id: str) -> list[HitlInterrupt]:
        return [i for i in self._interrupts.values() if i.engagement_id == engagement_id]


class FakeMessageRepository:
    def __init__(self) -> None:
        self.messages: list[Message] = []
        self._next_id = 1

    async def create_message(self, message: Message) -> Message:
        stored = Message(
            id=f"msg-{self._next_id}",
            engagement_id=message.engagement_id,
            user_id=message.user_id,
            role=message.role,
            message_type=message.message_type,
            content=message.content,
            run_id=message.run_id,
            metadata=message.metadata,
        )
        self._next_id += 1
        self.messages.append(stored)
        return stored

    async def get_message(self, message_id: str) -> Message | None:
        return next((m for m in self.messages if m.id == message_id), None)

    async def list_messages(self, *, engagement_id: str, user_id: str) -> list[Message]:
        return [
            m for m in self.messages if m.engagement_id == engagement_id and m.user_id == user_id
        ]


def _service(
    router_llm: FakeLlmClient,
    *,
    orchestration_llm: FakeLlmClient | None = None,
    actions: list[CopilotAction] | None = None,
) -> tuple[CopilotChatService, FakeMessageRepository, FakeHitlRepository]:
    message_repository = FakeMessageRepository()
    hitl_repository = FakeHitlRepository()
    orchestration = OrchestrationService(
        llm_client=orchestration_llm or FakeLlmClient(),
        api_client=FakeApiClient(),
        run_repository=FakeRunRepository(),
        hitl_repository=hitl_repository,
        checkpointer=InMemorySaver(),
        default_model="fast-model",
        max_replans=2,
    )
    service = CopilotChatService(
        llm_client=router_llm,
        message_repository=message_repository,
        hitl_repository=hitl_repository,
        orchestration_service=orchestration,
        default_model="fast-model",
        actions=actions,
    )
    return service, message_repository, hitl_repository


def _investigate_router_llm() -> FakeLlmClient:
    return FakeLlmClient(
        default_text=(
            '{"type": "investigate", "use_case": "fraud_triage", '
            '"task": "Investigate the duplicate payments.", "content": "Let me look into that."}'
        )
    )


def _clean_investigation_llm() -> FakeLlmClient:
    """Drives one clean pass through the real graph to a HITL pause — same queued sequence
    test_orchestrator.py/test_graph.py use for the same reason."""
    return FakeLlmClient(
        default_text="a cited draft", responses=['["retrieval"]', "a cited draft", "0.95"]
    )


async def test_send_message_answers_directly() -> None:
    router_llm = FakeLlmClient(
        default_text='{"type": "answer", "content": "Go to Reports and click Generate report."}'
    )
    service, _, _ = _service(router_llm)

    messages = await service.send_message(
        engagement_id="e1", user_id="u1", content="How do I generate a report?"
    )

    assert len(messages) == 2
    assert messages[0].role == MessageRole.USER
    assert messages[0].content == "How do I generate a report?"
    assert messages[1].role == MessageRole.ASSISTANT
    assert messages[1].message_type == MessageType.TEXT
    assert "Generate report" in messages[1].content


async def test_send_message_falls_back_to_answer_on_unparseable_router_response() -> None:
    router_llm = FakeLlmClient(default_text="Sure, click Reports then Generate report.")
    service, _, _ = _service(router_llm)

    messages = await service.send_message(engagement_id="e1", user_id="u1", content="how do I do X")

    assert messages[1].message_type == MessageType.TEXT
    assert "Generate report" in messages[1].content


async def test_send_message_invokes_a_direct_action_grounded_in_the_real_result() -> None:
    async def fake_scan(*, engagement_id: str) -> dict[str, object]:
        return {"anomalies": [{"id": "a1"}, {"id": "a2"}]}

    action = CopilotAction(
        name="run_risk_scan",
        description="run the anomaly scan",
        handler=fake_scan,
        result_formatter=lambda result: f"Scan complete — {len(result['anomalies'])} anomaly(ies).",  # type: ignore[arg-type]
    )
    router_llm = FakeLlmClient(
        default_text=(
            '{"type": "action", "tool": "run_risk_scan", "arguments": {}, '
            '"content": "Running the scan..."}'
        )
    )
    service, _, _ = _service(router_llm, actions=[action])

    messages = await service.send_message(
        engagement_id="e1", user_id="u1", content="run the anomaly scan"
    )

    assert messages[1].message_type == MessageType.ACTION_RESULT
    assert "2 anomaly(ies)" in messages[1].content
    assert messages[1].metadata is not None
    assert messages[1].metadata["tool"] == "run_risk_scan"


async def test_send_message_surfaces_a_real_action_failure_honestly() -> None:
    async def failing_action(*, engagement_id: str) -> dict[str, object]:
        raise RuntimeError("apps/api unreachable")

    action = CopilotAction(
        name="run_risk_scan",
        description="run the anomaly scan",
        handler=failing_action,
        result_formatter=lambda result: "should not be reached",
    )
    router_llm = FakeLlmClient(
        default_text=(
            '{"type": "action", "tool": "run_risk_scan", "arguments": {}, '
            '"content": "Running the scan..."}'
        )
    )
    service, _, _ = _service(router_llm, actions=[action])

    messages = await service.send_message(
        engagement_id="e1", user_id="u1", content="run the anomaly scan"
    )

    assert messages[1].message_type == MessageType.ACTION_RESULT
    assert "failed" in messages[1].content
    assert "apps/api unreachable" in messages[1].content


async def test_send_message_hands_off_an_investigation_and_records_a_run_reference() -> None:
    service, _, hitl_repository = _service(
        _investigate_router_llm(), orchestration_llm=_clean_investigation_llm()
    )

    messages = await service.send_message(
        engagement_id="e1", user_id="u1", content="Look into the duplicate payments."
    )

    assistant_message = messages[1]
    assert assistant_message.message_type == MessageType.RUN_REFERENCE
    assert assistant_message.run_id is not None
    assert "waiting on your review" in assistant_message.content
    open_interrupts = await hitl_repository.list_open_for_run(assistant_message.run_id)
    assert len(open_interrupts) == 1


async def test_resolve_checkpoint_approve_completes_the_run_and_records_a_message() -> None:
    service, _, _ = _service(
        _investigate_router_llm(), orchestration_llm=_clean_investigation_llm()
    )
    messages = await service.send_message(engagement_id="e1", user_id="u1", content="Investigate.")
    run_reference_message = messages[1]

    resolution = await service.resolve_checkpoint(
        engagement_id="e1",
        user_id="u1",
        message_id=run_reference_message.id,
        decision=HitlDecision.APPROVE,
        reason=None,
    )

    assert "ready" in resolution.content
    assert resolution.metadata is not None
    assert resolution.metadata["run_status"] == "completed"


async def test_resolve_checkpoint_reject_requires_a_reason() -> None:
    service, _, _ = _service(
        _investigate_router_llm(), orchestration_llm=_clean_investigation_llm()
    )
    messages = await service.send_message(engagement_id="e1", user_id="u1", content="Investigate.")
    run_reference_message = messages[1]

    with pytest.raises(ValidationError):
        await service.resolve_checkpoint(
            engagement_id="e1",
            user_id="u1",
            message_id=run_reference_message.id,
            decision=HitlDecision.REJECT,
            reason=None,
        )


async def test_resolve_checkpoint_rejects_a_message_from_a_different_engagement() -> None:
    service, _, _ = _service(
        _investigate_router_llm(), orchestration_llm=_clean_investigation_llm()
    )
    messages = await service.send_message(engagement_id="e1", user_id="u1", content="Investigate.")
    run_reference_message = messages[1]

    with pytest.raises(NotFoundError):
        await service.resolve_checkpoint(
            engagement_id="e2",
            user_id="u1",
            message_id=run_reference_message.id,
            decision=HitlDecision.APPROVE,
            reason=None,
        )


async def test_list_messages_scopes_to_the_calling_user() -> None:
    service, _, _ = _service(FakeLlmClient(default_text='{"type": "answer", "content": "hi"}'))
    await service.send_message(engagement_id="e1", user_id="u1", content="hello")
    await service.send_message(engagement_id="e1", user_id="u2", content="hello")

    messages = await service.list_messages(engagement_id="e1", user_id="u1")

    assert len(messages) == 2  # this user's own user+assistant turn only
    assert all(m.user_id == "u1" for m in messages)
