"""The orchestration service — the application-layer entry point a run starts and resumes through.

Composes the three pieces the graph needs: the injected :class:`LlmClient` (real gateway or fake),
the :class:`RunRepository` (persists the ``agent.runs`` row), and a checkpointer. It builds the
graph, records the run, and drives one execution up to the human-in-the-loop interrupt — then
stops, because resumption is a separate act that may happen minutes or days later from a fresh
process: an interrupt is a durable pause, not an in-memory hold. ``resume_after_review`` is that
separate act — it feeds a reviewer's decision back into the same checkpointed thread, wherever the
graph left off.

What this service does *not* do: make the agents produce real audit findings. Every LLM-calling
node routes through the injected ``LlmClient``; with the real gateway and no API key that raises a
clean ``LlmProviderNotConfiguredError`` at the first model call (the Planner). So ``start_run``
persists the run, builds the graph, and — in an unconfigured environment — surfaces that typed 503
rather than fabricating output. That is the built-vs-unverifiable line for this scaffold.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from agent_orchestrator.application.graph import GraphState, build_graph
from agent_orchestrator.application.nodes import AgentNodes
from agent_orchestrator.domain.agents import HitlDecision, RunStatus
from agent_orchestrator.domain.entities import AgentRun, HitlInterrupt
from agent_orchestrator.domain.ports import HitlRepository, LlmClient, RagApiClient, RunRepository
from agent_orchestrator.shared.logging import get_logger

logger = get_logger(__name__)


class OrchestrationService:
    """Starts and resumes agent runs over the compiled LangGraph graph."""

    def __init__(
        self,
        *,
        llm_client: LlmClient,
        api_client: RagApiClient,
        run_repository: RunRepository,
        hitl_repository: HitlRepository,
        checkpointer: BaseCheckpointSaver[Any],
        default_model: str,
        max_replans: int,
    ) -> None:
        self._llm_client = llm_client
        self._run_repository = run_repository
        self._hitl_repository = hitl_repository
        self._nodes = AgentNodes(llm_client, api_client, default_model=default_model)
        self._graph = build_graph(self._nodes, checkpointer=checkpointer, max_replans=max_replans)

    async def _classify_terminal_status(self, config: RunnableConfig) -> RunStatus:
        """Inspects the checkpoint's own ``next`` pointer to classify where the graph actually
        stopped, rather than guessing.

        ``next`` is non-empty exactly when the compiled graph's ``interrupt_before=[_HITL]`` fired
        — a genuine, durable pause — so a non-empty ``next`` is unambiguously
        ``AWAITING_HUMAN_REVIEW``. Every other stopping point is a real ``END``, and the graph's own
        topology (``application/graph.py``) makes the two ``END``-reaching paths distinguishable
        from the final state alone: a hard Guardrail stop always leaves a ``violation``-severity
        flag in ``guardrail_flags`` (the only way to route to ``END`` other than through HITL or
        cannot-conclude); anything else that reached ``END`` without pausing did so via the
        evaluation-driven ``cannot_conclude`` route — the honest "could not reach a confident
        answer" terminal.
        """
        snapshot = await self._graph.aget_state(config)
        if snapshot.next:
            return RunStatus.AWAITING_HUMAN_REVIEW

        flags = snapshot.values.get("guardrail_flags", [])
        if any(flag.get("severity") == "violation" for flag in flags):
            return RunStatus.HALTED
        return RunStatus.CANNOT_CONCLUDE

    async def start_run(
        self, *, engagement_id: str, use_case: str, task: str, initiated_by: str
    ) -> AgentRun:
        """Persist a new run and drive the graph to its first stopping point.

        Records the ``agent.runs`` row first (so the run is durable and RLS-scoped before any work),
        then invokes the graph. The graph's ``thread_id`` is the run id — that is what ties every
        checkpoint and any resume back to this specific run. In an unconfigured
        environment the first LLM node raises ``LlmProviderNotConfiguredError``, the status is set
        to ``FAILED``, and the error propagates to the caller as a 503 — the run row still exists,
        recording the attempt.
        """
        run = await self._run_repository.create_run(
            AgentRun(
                id="",  # assigned by the DB default; the returned entity carries the real id
                engagement_id=engagement_id,
                use_case=use_case,
                status=RunStatus.RUNNING,
                initiated_by=initiated_by,
                task=task,
            )
        )
        config: RunnableConfig = {"configurable": {"thread_id": run.id}}
        initial_state: GraphState = {
            "run_id": run.id,
            "engagement_id": engagement_id,
            "initiated_by": initiated_by,
            "task": task,
        }
        try:
            await self._graph.ainvoke(initial_state, config)
        except Exception:
            await self._run_repository.set_status(run.id, RunStatus.FAILED)
            logger.warning("run_failed", run_id=run.id, use_case=use_case)
            raise

        status = await self._classify_terminal_status(config)
        await self._run_repository.set_status(run.id, status)
        if status == RunStatus.AWAITING_HUMAN_REVIEW:
            # The queryable "pending human decision" record — distinct from the graph's own
            # checkpoint (which is what actually resumes execution). Without this row,
            # `GET .../hitl-interrupts` would have nothing to show a reviewing UI even though the
            # graph really is paused; `resume_after_review` still works off the run id alone, but a
            # reviewer needs something to list and to attach their reason/decision to.
            await self._hitl_repository.open_interrupt(
                HitlInterrupt(
                    id="",
                    run_id=run.id,
                    engagement_id=engagement_id,
                    step_name="hitl",
                )
            )
        return AgentRun(
            id=run.id,
            engagement_id=run.engagement_id,
            use_case=run.use_case,
            status=status,
            initiated_by=run.initiated_by,
            task=run.task,
            created_at=run.created_at,
        )

    async def resume_after_review(self, *, run_id: str, decision: HitlDecision) -> RunStatus:
        """Feeds a reviewer's decision back into a run paused at the HITL interrupt.

        ``Command(resume=...)`` is what LangGraph delivers as the return value of the in-node
        ``interrupt(...)`` call (``application/graph.py``'s ``_hitl_node``) — the graph continues
        from exactly the checkpoint it paused at, in this or an entirely different process from the
        one that started it, which is the whole point of a durable interrupt. Approval routes to
        Report Generation and ends ``COMPLETED``; rejection or an edit ends the run without a
        published report — both the AI's draft and the human's decision remain in the checkpoint
        history either way, this method only advances the durable ``agent.runs.status`` to match.

        Rejection/edit maps to ``HALTED`` rather than a dedicated "human rejected" status: the
        domain model (``domain/agents.py``) does not yet distinguish "a reviewer declined to
        publish" from "a guardrail hard-stopped the run" as separate terminal states — both mean
        "this run ends with no approved report," and the *reviewer's own decision* (with their
        reason, on ``agent.hitl_interrupts``) is the durable record of which one actually happened.
        Adding a dedicated status is a small, real schema change, named here as a known gap rather
        than done silently.
        """
        config: RunnableConfig = {"configurable": {"thread_id": run_id}}
        try:
            await self._graph.ainvoke(Command(resume=decision.value), config)
        except Exception:
            await self._run_repository.set_status(run_id, RunStatus.FAILED)
            logger.warning("resume_failed", run_id=run_id)
            raise

        status = RunStatus.COMPLETED if decision == HitlDecision.APPROVE else RunStatus.HALTED
        await self._run_repository.set_status(run_id, status)
        return status
