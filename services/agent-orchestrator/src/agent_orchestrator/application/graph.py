"""The master LangGraph state graph (Phase 5 Fig. 1) — nodes, edges, reducers, HITL, checkpointing.

This is the one module that actually touches LangGraph's ``StateGraph`` API. It assembles the exact
topology Phase 5 Fig. 1 draws:

    START -> guardrail_in -> planner
          -> [fan-out: retrieval | sql | knowledge_graph | fraud_detection]  (parallel, §13)
          -> context_engineering (join barrier)  -> evaluation
          -> {proceed -> guardrail_out | replan -> planner | cannot_conclude -> END}   (§8, §14)
    guardrail_out -> {halt -> END | await_review -> hitl}                               (§9)
    hitl (interrupt, §15) -> {report -> report_generation -> END | end -> END}

Two design points worth stating up front, both from Phase 5:

    * The reducers from ``domain/state.py`` are bound to the state channels here, via
      ``typing.Annotated``. This is the deliberate seam: the reducer *semantics* are framework-free
      domain functions (unit-tested without LangGraph); their *binding* to LangGraph's channel
      machinery lives here in the application layer, one level up. Ports-and-adapters applied to the
      state schema itself.
    * The fan-out is a set of direct conditional edges from the planner to each wanted specialist,
      and each specialist edges to ``context_engineering``. LangGraph runs the dispatched
      specialists in one superstep (parallel, §13) and ``context_engineering`` is the natural join —
      it does not run until every inbound specialist edge has delivered, because a node with
      multiple inbound edges waits for all of them. The append reducers (§11) make those concurrent
      writes race-free.

The graph is compiled with an injected checkpointer (``InMemorySaver`` in tests and this scaffold's
DI wiring — see ``application/orchestrator.py``) and interrupts before the ``hitl`` node so a run
genuinely pauses and checkpoints there (§15, §16), resumable minutes or days later.
"""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from agent_orchestrator.application.nodes import AgentNodes
from agent_orchestrator.application.routing import (
    route_after_evaluation,
    route_after_guardrail_in,
    route_after_guardrail_out,
    route_after_hitl,
    specialists_for_plan,
)
from agent_orchestrator.domain.agents import AgentRole
from agent_orchestrator.domain.state import (
    PlanStep,
    append_reducer,
    increment_reducer,
    overwrite_reducer,
)

# The LangGraph-facing state schema: the same twelve channels as domain.state.AgentState, each
# bound to its Phase 5 §11 reducer via Annotated. This lives here (not in domain) precisely because
# `Annotated[..., reducer]` is the LangGraph binding — the domain owns the reducer functions and the
# plain channel shapes; this is where they meet the framework.
#
# `run_id` / `engagement_id` / `initiated_by` carry no reducer: they are set once at entry and never
# merged (LangGraph's default last-value channel is correct for a value only ever written once).


class GraphState(TypedDict, total=False):
    run_id: str
    engagement_id: str
    initiated_by: str

    task: str
    plan: Annotated[list[PlanStep], overwrite_reducer]
    evidence_retrieval: Annotated[list[dict[str, Any]], append_reducer]
    sql_results: Annotated[list[dict[str, Any]], append_reducer]
    graph_context: Annotated[list[dict[str, Any]], append_reducer]
    fraud_signals: Annotated[dict[str, Any], overwrite_reducer]
    engineered_context: Annotated[str, overwrite_reducer]
    evaluation_score: Annotated[float, overwrite_reducer]
    guardrail_flags: Annotated[list[dict[str, Any]], append_reducer]
    hitl_decision: str
    retry_count: Annotated[int, increment_reducer]
    final_output: Annotated[dict[str, Any], overwrite_reducer]
    submitted_finding_id: Annotated[str, overwrite_reducer]


# Node name constants — one authoritative spelling per node, referenced by both add_node and the
# edge wiring, so a rename can never desync a node from an edge that targets it.
_GUARDRAIL_IN = "guardrail_in"
_PLANNER = AgentRole.PLANNER.value
_CONTEXT_ENGINEERING = AgentRole.CONTEXT_ENGINEERING.value
_EVALUATION = AgentRole.EVALUATION.value
_GUARDRAIL_OUT = "guardrail_out"
_HITL = "hitl"
_REPORT = AgentRole.REPORT_GENERATION.value

# The evidence-specialist node names, keyed by role, in declaration order.
_SPECIALIST_NODES: dict[AgentRole, str] = {
    AgentRole.RETRIEVAL: AgentRole.RETRIEVAL.value,
    AgentRole.SQL: AgentRole.SQL.value,
    AgentRole.KNOWLEDGE_GRAPH: AgentRole.KNOWLEDGE_GRAPH.value,
    AgentRole.FRAUD_DETECTION: AgentRole.FRAUD_DETECTION.value,
}


async def _hitl_node(state: GraphState) -> GraphState:
    """The human-in-the-loop interrupt node (Phase 5 §15).

    Calls LangGraph's ``interrupt(...)`` — a genuine graph pause, not a UI-side hold. When the graph
    reaches here it checkpoints its full state and raises out of execution; the process may
    terminate entirely while awaiting a reviewer (§15). Resumption feeds the reviewer's decision
    back in via ``Command(resume=...)`` (``orchestrator.resume_after_review``); ``interrupt``
    returns that value, written to ``hitl_decision`` for ``route_after_hitl`` to read.

    The payload passed to ``interrupt`` is what a reviewing UI receives to render the pending
    decision — here, the draft under review and any soft guardrail flags that forced the interrupt.
    """
    decision = interrupt(
        {
            "run_id": state.get("run_id"),
            "engineered_context": state.get("engineered_context", ""),
            "guardrail_flags": state.get("guardrail_flags", []),
        }
    )
    return {"hitl_decision": str(decision)}


def build_graph(
    nodes: AgentNodes,
    *,
    checkpointer: BaseCheckpointSaver[Any],
    max_replans: int,
) -> CompiledStateGraph[GraphState, Any, Any, Any]:
    """Assemble and compile the master graph (Phase 5 Fig. 1).

    ``nodes`` carries the injected ``LlmClient`` (real gateway or fake); ``checkpointer`` is the
    injected state-persistence backend; ``max_replans`` is the §14 re-plan ceiling threaded into
    the post-evaluation router. Returns the compiled graph ready to ``ainvoke`` — building the
    graph never makes an LLM call, so this succeeds with no API key; only *running* it to an
    LLM node would need one.
    """
    graph: StateGraph[GraphState, Any, Any, Any] = StateGraph(GraphState)

    # --- nodes (Phase 5 §1-§9) ---
    graph.add_node(_GUARDRAIL_IN, nodes.guardrail_in)
    graph.add_node(_PLANNER, nodes.planner)
    graph.add_node(AgentRole.RETRIEVAL.value, nodes.retrieval)
    graph.add_node(AgentRole.SQL.value, nodes.sql)
    graph.add_node(AgentRole.KNOWLEDGE_GRAPH.value, nodes.knowledge_graph)
    graph.add_node(AgentRole.FRAUD_DETECTION.value, nodes.fraud_detection)
    graph.add_node(_CONTEXT_ENGINEERING, nodes.context_engineering)
    graph.add_node(_EVALUATION, nodes.evaluation)
    graph.add_node(_GUARDRAIL_OUT, nodes.guardrail_out)
    graph.add_node(_HITL, _hitl_node)
    graph.add_node(_REPORT, nodes.report_generation)

    # --- entry: START -> guardrail-in -> {hard-stop on injection | planner} (§1, §9, §17) ---
    # The input guardrail scans the incoming task for prompt-injection *before* any evidence is
    # gathered (§9 — so injected content never reaches a reasoning agent's context). A violation is
    # a hard stop, exactly as the output guardrail's violation is (§17) — never a retry, because
    # retrying a security event is itself a risk (§9). Without this edge the input scan would flag
    # but nothing would act on the flag; Phase 5 §9 places the input scan here precisely so it can
    # halt the run at entry.
    graph.add_edge(START, _GUARDRAIL_IN)
    graph.add_conditional_edges(
        _GUARDRAIL_IN,
        route_after_guardrail_in,
        {"halt": END, "proceed": _PLANNER},
    )

    # --- fan-out: planner -> the evidence specialists the plan wants, in parallel (§13) ---
    # A conditional edge whose router returns the *list* of specialist node names to dispatch;
    # LangGraph runs every returned target in one superstep. Returning a list (not a single label)
    # is how one conditional edge expresses parallel fan-out.
    def _fan_out(state: GraphState) -> list[str]:
        wanted = specialists_for_plan(state.get("plan", []))
        # A plan with no evidence specialist still needs to advance to the join, or the run would
        # dead-end before evaluation — route straight to context engineering in that (edge) case.
        if not wanted:
            return [_CONTEXT_ENGINEERING]
        return [_SPECIALIST_NODES[role] for role in wanted]

    graph.add_conditional_edges(
        _PLANNER,
        _fan_out,
        # The full set of possible targets — every specialist plus the no-specialist fallback — so
        # LangGraph validates the router can only return names that exist as nodes.
        [*_SPECIALIST_NODES.values(), _CONTEXT_ENGINEERING],
    )

    # --- join: every specialist -> context engineering (§7, §13) ---
    # context_engineering has multiple inbound edges, so LangGraph does not run it until every
    # dispatched specialist in the superstep has completed — this *is* the join barrier (§13).
    for specialist_node in _SPECIALIST_NODES.values():
        graph.add_edge(specialist_node, _CONTEXT_ENGINEERING)

    # --- context engineering -> evaluation (§7 -> §8) ---
    graph.add_edge(_CONTEXT_ENGINEERING, _EVALUATION)

    # --- post-evaluation: proceed / bounded re-plan / cannot-conclude (§8, §14, §17) ---
    def _after_eval(state: GraphState) -> str:
        return route_after_evaluation(state, max_replans=max_replans)

    graph.add_conditional_edges(
        _EVALUATION,
        _after_eval,
        {"proceed": _GUARDRAIL_OUT, "replan": _PLANNER, "cannot_conclude": END},
    )

    # --- output guardrail: hard-stop on violation, else to human review (§9, §17) ---
    graph.add_conditional_edges(
        _GUARDRAIL_OUT,
        route_after_guardrail_out,
        {"halt": END, "await_review": _HITL},
    )

    # --- HITL -> report on approval, else end (both versions preserved by the node) (§15) ---
    graph.add_conditional_edges(
        _HITL,
        route_after_hitl,
        {"report": _REPORT, "end": END},
    )

    graph.add_edge(_REPORT, END)

    # Compile with the injected checkpointer so every superstep writes a full state snapshot (§16),
    # and interrupt *before* the HITL node so the run genuinely pauses and checkpoints there,
    # resumable later (§15). `interrupt_before` is belt-and-suspenders alongside the in-node
    # `interrupt(...)` call: the compiled interrupt guarantees the pause even if a future refactor
    # changes the node body.
    return graph.compile(checkpointer=checkpointer, interrupt_before=[_HITL])
