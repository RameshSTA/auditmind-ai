"""CopilotChatService — the conversational layer behind AI Copilot's chat UI.

A deliberately separate, lightweight service from :class:`OrchestrationService`, not a new
LangGraph graph — see the design note this module's docstring below expands on. Every user message
gets exactly one router LLM call, which decides to (a) answer directly, (b) invoke one real direct
action (a plain HTTP call into apps/api, e.g. "run the anomaly scan"), or (c) hand off to the
existing, unmodified investigation graph (:meth:`OrchestrationService.start_run`) for anything that
needs evidence-gathering or could result in a drafted finding. Nothing here reimplements the
planner/specialist/evaluation/guardrail/HITL pipeline — that pipeline is reused whole.

Why a separate service rather than new graph nodes: the existing graph is a well-tested,
multi-step, *interruptible* investigation pipeline — the right shape for "gather evidence, draft a
finding, get sign-off." A chat turn like "hi" or "what does the Fraud Detection page do" needs one
decision and usually one action, not a planner, a context-engineering step, an LLM-as-judge
evaluation, or its own checkpoint. Forcing every message through the full pipeline would be slow,
expensive, and would put chit-chat through gates (evaluation, guardrail) that exist to gate
*findings*, not conversation.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agent_orchestrator.application.copilot_reference import PLATFORM_REFERENCE
from agent_orchestrator.application.orchestrator import OrchestrationService
from agent_orchestrator.domain.agents import HitlDecision, MessageRole, MessageType, RunStatus
from agent_orchestrator.domain.entities import Message, PromptMessage
from agent_orchestrator.domain.ports import HitlRepository, LlmClient, MessageRepository
from agent_orchestrator.shared.errors import NotFoundError, ValidationError
from agent_orchestrator.shared.logging import get_logger

logger = get_logger(__name__)

_ROUTER_SYSTEM_PROMPT_TEMPLATE = """\
You are AI Copilot, the assistant embedded in AuditMind AI, an internal-audit platform. Decide how \
to handle the auditor's message and respond with EXACTLY one JSON object and nothing else — no \
prose before or after it.

{platform_reference}

Direct actions you can invoke right now (use one of these when the message is clearly asking you \
to perform this one specific data operation):
{action_list}

Respond with exactly one of these three JSON shapes:
1. Answer directly (product help, or a quick evidence-grounded answer):
   {{"type": "answer", "content": "<your answer>"}}
2. Invoke exactly one direct action:
   {{"type": "action", "tool": "<tool name from the list above>", "arguments": {{}}, \
"content": "<one short line to show while it runs>"}}
3. Hand off to a full investigation (needs evidence-gathering and/or could result in a drafted \
finding requiring human sign-off):
   {{"type": "investigate", "use_case": "control_test or fraud_triage", "task": "<a clear, \
self-contained task description>", "content": "<one short line, e.g. 'Let me look into that.'>"}}

If a message doesn't cleanly supply what an action needs (e.g. importing transactions needs actual \
transaction data you don't have), use "answer" to ask for what's missing rather than guessing.\
"""


@dataclass(frozen=True)
class CopilotAction:
    """One direct action the chat router may invoke — a thin, named wrapper around a single real
    HTTP call into apps/api (the same pattern as the existing RAG tools, just outside the graph).

    ``result_formatter`` turns the tool's *real* response into the confirmation message shown in
    chat — never the model's own pre-call guess, so a "success" message can't be fabricated
    independently of what the backing endpoint actually did.
    """

    name: str
    description: str
    handler: Callable[..., Awaitable[dict[str, object]]]
    result_formatter: Callable[[dict[str, object]], str]


@dataclass(frozen=True)
class _Directive:
    kind: str  # "answer" | "action" | "investigate"
    content: str
    tool: str | None = None
    arguments: dict[str, object] = field(default_factory=dict)
    use_case: str | None = None
    task: str | None = None


def _extract_json_object(text: str) -> str | None:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else None


def _parse_directive(text: str) -> _Directive:
    """Parses the router LLM's response into a directive, falling back to a plain "answer" of the
    raw text for anything unparseable — the same "never crash on an unparseable model response,
    degrade to the safest interpretation" discipline ``nodes.py``'s Planner parsing already uses."""
    raw = _extract_json_object(text)
    if raw is None:
        return _Directive(kind="answer", content=text.strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _Directive(kind="answer", content=text.strip())
    if not isinstance(data, dict):
        return _Directive(kind="answer", content=text.strip())

    kind = data.get("type")
    content = str(data.get("content") or "").strip() or text.strip()

    if kind == "action" and isinstance(data.get("tool"), str):
        arguments = data.get("arguments")
        return _Directive(
            kind="action",
            content=content,
            tool=data["tool"],
            arguments=arguments if isinstance(arguments, dict) else {},
        )
    if kind == "investigate":
        use_case = data.get("use_case")
        task = data.get("task")
        return _Directive(
            kind="investigate",
            content=content,
            use_case=use_case if isinstance(use_case, str) else None,
            task=task if isinstance(task, str) else None,
        )
    return _Directive(kind="answer", content=content)


class CopilotChatService:
    def __init__(
        self,
        *,
        llm_client: LlmClient,
        message_repository: MessageRepository,
        hitl_repository: HitlRepository,
        orchestration_service: OrchestrationService,
        default_model: str,
        actions: list[CopilotAction] | None = None,
    ) -> None:
        self._llm = llm_client
        self._messages = message_repository
        self._hitl = hitl_repository
        self._orchestration = orchestration_service
        self._model = default_model
        self._actions = actions or []
        self._actions_by_name = {a.name: a for a in self._actions}

    def _system_prompt(self) -> str:
        action_list = (
            "\n".join(f"- {a.name}: {a.description}" for a in self._actions)
            or "(no direct actions configured yet)"
        )
        return _ROUTER_SYSTEM_PROMPT_TEMPLATE.format(
            platform_reference=PLATFORM_REFERENCE, action_list=action_list
        )

    async def _persist(
        self,
        *,
        engagement_id: str,
        user_id: str,
        role: MessageRole,
        message_type: MessageType,
        content: str,
        run_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Message:
        return await self._messages.create_message(
            Message(
                id=str(uuid.uuid4()),
                engagement_id=engagement_id,
                user_id=user_id,
                role=role,
                message_type=message_type,
                content=content,
                run_id=run_id,
                metadata=metadata,
            )
        )

    async def send_message(
        self, *, engagement_id: str, user_id: str, content: str
    ) -> list[Message]:
        """Persists the user's turn, routes it, and returns every message this turn produced (the
        user's own message plus whatever the assistant responded with — usually one message,
        occasionally the assistant's could grow to more than one in a future revision)."""
        user_message = await self._persist(
            engagement_id=engagement_id,
            user_id=user_id,
            role=MessageRole.USER,
            message_type=MessageType.TEXT,
            content=content,
        )

        router_response = await self._llm.complete(
            messages=[
                PromptMessage(role="system", content=self._system_prompt()),
                PromptMessage(role="user", content=content),
            ],
            model=self._model,
        )
        directive = _parse_directive(router_response.text)

        if directive.kind == "action":
            assistant_message = await self._dispatch_action(engagement_id, user_id, directive)
        elif directive.kind == "investigate":
            assistant_message = await self._dispatch_investigation(
                engagement_id, user_id, directive
            )
        else:
            assistant_message = await self._persist(
                engagement_id=engagement_id,
                user_id=user_id,
                role=MessageRole.ASSISTANT,
                message_type=MessageType.TEXT,
                content=directive.content,
            )
        return [user_message, assistant_message]

    async def _dispatch_action(
        self, engagement_id: str, user_id: str, directive: _Directive
    ) -> Message:
        action = self._actions_by_name.get(directive.tool or "")
        if action is None:
            return await self._persist(
                engagement_id=engagement_id,
                user_id=user_id,
                role=MessageRole.ASSISTANT,
                message_type=MessageType.TEXT,
                content=(
                    f"I tried to use a tool called \"{directive.tool}\", but it isn't available."
                ),
            )
        try:
            result = await action.handler(engagement_id=engagement_id, **directive.arguments)
        except Exception as exc:  # noqa: BLE001 — surfaced honestly in chat, not swallowed
            logger.warning("copilot_action_failed", tool=action.name, error=str(exc))
            return await self._persist(
                engagement_id=engagement_id,
                user_id=user_id,
                role=MessageRole.ASSISTANT,
                message_type=MessageType.ACTION_RESULT,
                content=f"I tried to {action.description.lower()}, but it failed: {exc}",
                metadata={"tool": action.name, "error": str(exc)},
            )
        summary = action.result_formatter(result)
        return await self._persist(
            engagement_id=engagement_id,
            user_id=user_id,
            role=MessageRole.ASSISTANT,
            message_type=MessageType.ACTION_RESULT,
            content=summary,
            metadata={"tool": action.name, "result": result},
        )

    async def _dispatch_investigation(
        self, engagement_id: str, user_id: str, directive: _Directive
    ) -> Message:
        run = await self._orchestration.start_run(
            engagement_id=engagement_id,
            use_case=directive.use_case or "fraud_triage",
            task=directive.task or directive.content,
            initiated_by=user_id,
        )
        if run.status == RunStatus.AWAITING_HUMAN_REVIEW:
            outcome = (
                "I've gathered evidence and drafted a response — it's waiting on your review "
                "below before anything is published."
            )
        elif run.status == RunStatus.COMPLETED:
            outcome = "Done — completed without needing your review."
        elif run.status == RunStatus.CANNOT_CONCLUDE:
            outcome = "I wasn't able to reach a confident conclusion with the evidence available."
        else:
            outcome = f"This didn't complete cleanly (status: {run.status.value})."
        return await self._persist(
            engagement_id=engagement_id,
            user_id=user_id,
            role=MessageRole.ASSISTANT,
            message_type=MessageType.RUN_REFERENCE,
            content=f"{directive.content}\n\n{outcome}",
            run_id=run.id,
            metadata={"run_status": run.status.value},
        )

    async def resolve_checkpoint(
        self,
        *,
        engagement_id: str,
        user_id: str,
        message_id: str,
        decision: HitlDecision,
        reason: str | None,
    ) -> Message:
        """Resolves the HITL checkpoint an earlier chat message (a ``RUN_REFERENCE``) handed off
        to — a thin wrapper around the same real resolution path the standalone
        ``/hitl-interrupts/.../resolve`` endpoint already uses, so the chat thread stays coherent
        (every resolution is a visible message) without a second implementation of the resolve
        logic. Takes only ``message_id``, not a separate run/interrupt id pair, so the chat UI
        never has to track LangGraph-internal identifiers of its own — it already has the message.
        """
        message = await self._messages.get_message(message_id)
        if (
            message is None
            or message.engagement_id != engagement_id
            or message.user_id != user_id
            or message.run_id is None
        ):
            raise NotFoundError(f"No resolvable checkpoint on message {message_id}.")

        open_interrupts = await self._hitl.list_open_for_run(message.run_id)
        if not open_interrupts:
            raise NotFoundError(f"No open checkpoint on run {message.run_id}.")
        interrupt = open_interrupts[0]

        if decision != HitlDecision.APPROVE and not reason:
            raise ValidationError("A reject or edit decision requires a documented reason.")

        await self._hitl.resolve(
            interrupt.id, decision=decision, reviewer_id=user_id, reason=reason
        )
        status = await self._orchestration.resume_after_review(
            run_id=message.run_id, decision=decision
        )

        if status == RunStatus.COMPLETED:
            outcome = (
                "Approved — recorded. The drafted finding is saved on the Findings page "
                "for your review; generating a report from it is a separate step on the "
                "Reports page whenever you're ready."
            )
        else:
            outcome = f"Recorded your {decision.value} decision."
        run_id = message.run_id
        return await self._persist(
            engagement_id=engagement_id,
            user_id=user_id,
            role=MessageRole.ASSISTANT,
            message_type=MessageType.TEXT,
            content=outcome,
            run_id=run_id,
            metadata={"run_status": status.value},
        )

    async def list_messages(self, *, engagement_id: str, user_id: str) -> list[Message]:
        return await self._messages.list_messages(engagement_id=engagement_id, user_id=user_id)
