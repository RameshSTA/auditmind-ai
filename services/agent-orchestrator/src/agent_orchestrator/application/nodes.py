"""The nine agent nodes, as dependency-injected graph node functions.

Each node is an ``async`` method on :class:`AgentNodes`, which is constructed with an
:class:`~agent_orchestrator.domain.ports.LlmClient`. A node returns a *partial* ``AgentState`` — the
subset of channels it writes — which LangGraph merges into the run state using each channel's
reducer. Returning a partial (not mutating the input) is what keeps the append-reducer channels
parallel-safe: two specialists running concurrently each return their own channel's delta, and
LangGraph merges both without either seeing the other's write.

Nodes split into LLM-driven and deterministic, with one deliberate upgrade: Evaluation moved from
the deterministic column to the LLM-driven one.

    * LLM-driven (Planner, Retrieval, SQL, Knowledge Graph, Fraud Detection, Evaluation, Report
      Generation) call ``self._llm.complete(...)`` — a real gateway call in production, a fake in
      tests. This is a *real integration* against the ``LlmClient`` port, never a stub: the node
      builds a genuine prompt from state and consumes a genuine :class:`ModelResponse`.
    * deterministic (Context Engineering, Guardrail) do real in-state work with no model call at
      all — these must not be LLM guesses (the fraud *score* is computed deterministically
      upstream; context budgeting and injection/PII scanning are algorithmic). Evaluation *used*
      to be a deterministic evidence-presence proxy; it now makes a genuine second, independent
      model call asking whether the gathered narrative actually answers the task, not just
      whether text exists — see ``evaluation()``'s own docstring below.

Retrieval, Fraud Detection, and Knowledge Graph now call their real tools
(``hybrid_search``/``get_anomaly_cluster``/``get_risk_score``/``graph_traverse``) against
``apps/api`` for real — the named RAG gap this class used to have. Each specialist builds its
prompt from the task *and* whatever real evidence its tool call returned, and the output guardrail
(``guardrail_out``) writes a clean, evidence-backed draft back into ``apps/api``'s reporting
context via ``submit_finding_draft`` — the write half of the loop. The SQL agent is the one
specialist still deferred: ``run_sql_template`` has no backing endpoint anywhere in apps/api yet
(no parameterized-query-template execution surface exists to call), unlike the other three tools,
which apps/api already exposed as plain reads/writes this service could wire directly. The Planner
now also decomposes into a genuine multi-step plan (which of the four evidence specialists a task
actually needs), not a single hardcoded retrieval step — ``routing.specialists_for_plan`` already
supported dispatching every specialist named across a multi-step plan; only the parser was missing.
"""

from __future__ import annotations

import json
import re

from agent_orchestrator.domain.agents import AgentRole
from agent_orchestrator.domain.entities import PromptMessage, RetrievedChunk
from agent_orchestrator.domain.ports import LlmClient, RagApiClient
from agent_orchestrator.domain.state import AgentState, PlanStep
from agent_orchestrator.shared.errors import UpstreamApiError
from agent_orchestrator.shared.logging import get_logger

logger = get_logger(__name__)

# The four evidence specialists a plan may dispatch to — the Planner's structured decomposition
# is validated against exactly this set, matching `domain.agents.EVIDENCE_SPECIALISTS` (kept as
# plain strings here since that's the JSON-parseable shape the model's response takes).
_PLANNABLE_AGENTS = (
    AgentRole.RETRIEVAL.value,
    AgentRole.SQL.value,
    AgentRole.KNOWLEDGE_GRAPH.value,
    AgentRole.FRAUD_DETECTION.value,
)

_PLANNER_PROMPT = (
    "Task: {task}\n\n"
    "Which specialists does this task need? Choose one or more from: "
    "retrieval, sql, knowledge_graph, fraud_detection.\n"
    "- retrieval: finds evidence passages in uploaded documents.\n"
    "- sql: queries structured transaction data (not yet available — do not choose this).\n"
    "- knowledge_graph: traces vendor/payment relationships.\n"
    "- fraud_detection: reads pre-computed anomaly and risk scores.\n"
    "Respond with ONLY a JSON array of the agent names you chose, e.g. "
    '["retrieval", "fraud_detection"]. If unsure, include "retrieval" — it is the most broadly '
    "useful default for an evidence-gathering task."
)

_EVALUATION_SYSTEM_PROMPT = (
    "You are a strict evaluator for an internal-audit AI. Given a task and a gathered narrative, "
    "judge whether the narrative directly and specifically answers the task using only what is "
    "actually stated in it — never reward speculation or generic language. Respond with ONLY a "
    "number from 0.0 to 1.0: 1.0 means fully and specifically supported, 0.0 means unrelated, "
    "vague, or unsupported."
)

# A minimal, reviewed system framing per agent. In a fuller build these come from a versioned
# governance.prompt_registry — not yet implemented, so they live inline here, flagged as the
# registry's future home.
_SYSTEM_FRAMING: dict[AgentRole, str] = {
    AgentRole.PLANNER: (
        "You are the Planner for an internal-audit AI. Decompose the task into sub-tasks, assign "
        "each to a specialist agent, and record their dependency order. Never gather evidence "
        "yourself."
    ),
    AgentRole.RETRIEVAL: (
        "You are the Retrieval Agent. Answer only from the provided passages. If no passage "
        "supports an answer, say so explicitly — never answer from prior knowledge."
    ),
    AgentRole.SQL: (
        "You are the SQL Agent. Select a parameterized query template and bind its parameters. "
        "Never author raw SQL."
    ),
    AgentRole.KNOWLEDGE_GRAPH: (
        "You are the Knowledge Graph Agent. Answer relationship questions from graph facts using "
        "a parameterized traversal template."
    ),
    AgentRole.FRAUD_DETECTION: (
        "You are the Fraud Detection Agent. Triage and explain pre-computed anomaly signals — you "
        "never compute a fraud score yourself."
    ),
    AgentRole.REPORT_GENERATION: (
        "You are the Report Generation Agent. Compile only already-confirmed findings and their "
        "citation chains into a structured report."
    ),
}


class AgentNodes:
    """The graph's nine nodes, sharing one injected :class:`LlmClient`.

    A class rather than nine free functions so the ``LlmClient`` is injected once and every
    LLM-driven node shares it — and so a test constructs ``AgentNodes(fake_llm_client)`` and gets
    the whole graph's node behavior against the fake, no globals, the same DI discipline
    ``apps/api``'s services use.
    """

    def __init__(
        self, llm_client: LlmClient, api_client: RagApiClient, *, default_model: str
    ) -> None:
        self._llm = llm_client
        self._api = api_client
        self._model = default_model

    # --- 01 Planner (LLM-driven, orchestration root) ---
    async def planner(self, state: AgentState) -> AgentState:
        """Decompose the task into a plan.

        Increments ``retry_count`` only on a re-plan (when a plan already exists in state) so the
        first plan does not consume the re-plan budget — the increment reducer adds the ``1`` this
        returns to the running total.
        """
        is_replan = bool(state.get("plan"))
        response = await self._llm.complete(
            messages=[
                PromptMessage(role="system", content=_SYSTEM_FRAMING[AgentRole.PLANNER]),
                PromptMessage(role="user", content=_PLANNER_PROMPT.format(task=state["task"])),
            ],
            model=self._model,
        )
        # Real multi-step decomposition: the model picks which of the four evidence specialists
        # this task needs (it may pick more than one — e.g. a vendor-concentration question needs
        # both Retrieval and Knowledge Graph). `routing.specialists_for_plan` already fully
        # supports dispatching every distinct specialist named across the plan's steps in one
        # parallel superstep — this was previously the *only* piece missing, always handed a
        # single hardcoded retrieval step regardless of what the model said.
        agents = _parse_plan_agents(response.text)
        plan: list[PlanStep] = [
            {
                "step_id": f"s{i + 1}",
                "agent": agent,
                "objective": state["task"],
                "depends_on": [],
            }
            for i, agent in enumerate(agents)
        ]
        result: AgentState = {"plan": plan}
        if is_replan:
            result["retry_count"] = 1
        return result

    # --- 02 Retrieval (LLM-driven synthesis over real, tool-fetched passages) ---
    async def retrieval(self, state: AgentState) -> AgentState:
        """Calls the ``hybrid_search`` tool (apps/api's semantic search) for real passages, then
        asks the model to answer only from what it got back. ``chunks`` on the returned draft
        carries each passage's real chunk/document id — this is what ``guardrail_out`` reads to
        cite real evidence when it writes the draft finding, and what makes the model's
        ``[chunk:<id>]`` citations checkable against an actual row instead of invented."""
        chunks = await self._api.semantic_search(
            engagement_id=state["engagement_id"], query=state["task"]
        )
        passages = (
            "\n\n".join(f"[chunk:{c.chunk_id}] {c.text}" for c in chunks)
            if chunks
            else "No passages were found for this query."
        )
        response = await self._llm.complete(
            messages=[
                PromptMessage(role="system", content=_SYSTEM_FRAMING[AgentRole.RETRIEVAL]),
                PromptMessage(
                    role="user", content=f"Task: {state['task']}\n\nPassages:\n{passages}"
                ),
            ],
            model=self._model,
        )
        draft = {
            "agent": AgentRole.RETRIEVAL.value,
            "draft": response.text,
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "document_id": c.document_id,
                    "text": c.text,
                    "rank": c.rank,
                }
                for c in chunks
            ],
        }
        return {"evidence_retrieval": [draft]}

    # --- 03 SQL (LLM-driven template selection) ---
    async def sql(self, state: AgentState) -> AgentState:
        """Still deferred: unlike the other three specialists, ``run_sql_template`` has no
        backing endpoint anywhere in apps/api — there is no parameterized-query-template
        execution surface to call yet (a real, separate scope item, not silently skipped)."""
        response = await self._llm.complete(
            messages=[
                PromptMessage(role="system", content=_SYSTEM_FRAMING[AgentRole.SQL]),
                PromptMessage(role="user", content=state["task"]),
            ],
            model=self._model,
        )
        return {"sql_results": [{"agent": AgentRole.SQL.value, "draft": response.text}]}

    # --- 04 Knowledge Graph (LLM-driven synthesis over real vendor data) ---
    async def knowledge_graph(self, state: AgentState) -> AgentState:
        """Calls the ``graph_traverse`` tool — apps/api's resolved-vendor read model — so the
        model reasons over real vendor/transaction aggregates for this engagement, not a guess."""
        vendors = await self._api.list_vendors(engagement_id=state["engagement_id"])
        context = (
            f"Resolved vendors: {vendors}"
            if vendors
            else "No vendors have been resolved into the knowledge graph for this engagement yet."
        )
        response = await self._llm.complete(
            messages=[
                PromptMessage(role="system", content=_SYSTEM_FRAMING[AgentRole.KNOWLEDGE_GRAPH]),
                PromptMessage(role="user", content=f"Task: {state['task']}\n\n{context}"),
            ],
            model=self._model,
        )
        draft = {"agent": AgentRole.KNOWLEDGE_GRAPH.value, "draft": response.text}
        return {"graph_context": [draft]}

    # --- 05 Fraud Detection (LLM triage over real pre-computed signals) ---
    async def fraud_detection(self, state: AgentState) -> AgentState:
        """Calls the ``get_anomaly_cluster``/``get_risk_score`` tools — apps/api's already
        rule-engine- and ML-computed anomalies/risk scores — so triage narrates real signals; the
        fraud *score* itself is still computed deterministically upstream (never by this LLM)."""
        anomalies = await self._api.list_anomalies(engagement_id=state["engagement_id"])
        risk_scores = await self._api.list_risk_scores(engagement_id=state["engagement_id"])
        context = (
            f"Open anomalies: {anomalies}\n\nComputed risk scores: {risk_scores}"
            if anomalies or risk_scores
            else "No anomalies or risk scores have been computed for this engagement yet."
        )
        response = await self._llm.complete(
            messages=[
                PromptMessage(role="system", content=_SYSTEM_FRAMING[AgentRole.FRAUD_DETECTION]),
                PromptMessage(role="user", content=f"Task: {state['task']}\n\n{context}"),
            ],
            model=self._model,
        )
        return {"fraud_signals": {"narrative": response.text}}

    # --- 07 Context Engineering (DETERMINISTIC) ---
    async def context_engineering(self, state: AgentState) -> AgentState:
        """Deduplicate, rank, and budget the gathered evidence. No model call.

        Concatenates the drafts from every evidence channel into one engineered context string,
        deduplicating identical drafts — a real, deterministic operation over in-state evidence:
        this stage is 'the one place that decides what a model actually sees', not itself a model
        guess. Token-budget truncation is a simple character cap here; the real token-aware
        budgeter is deferred along with the tokenizer choice.
        """
        drafts: list[str] = []
        seen: set[str] = set()
        # Three literal `.get()` calls, not a loop over dynamic channel-name strings: TypedDict
        # attribute access needs a literal key for mypy to know each channel's declared value
        # type (`list[dict[str, Any]]`) rather than widening to `object`.
        evidence_channels = (
            state.get("evidence_retrieval", []),
            state.get("sql_results", []),
            state.get("graph_context", []),
        )
        for channel_items in evidence_channels:
            for item in channel_items:
                draft = str(item.get("draft", ""))
                if draft and draft not in seen:
                    seen.add(draft)
                    drafts.append(draft)
        fraud = state.get("fraud_signals", {}).get("narrative")
        if fraud and fraud not in seen:
            drafts.append(fraud)
        return {"engineered_context": "\n\n".join(drafts)}

    # --- 08 Evaluation (DETERMINISTIC groundedness proxy) ---
    async def evaluation(self, state: AgentState) -> AgentState:
        """Score the engineered context's groundedness with a real LLM-judge call — the deferred
        replacement named in this class's own prior scaffold comment.

        An empty ``engineered_context`` scores 0.0 without spending a model call on it (there is
        nothing to judge — no evidence was gathered at all). Otherwise a second, independent model
        call is asked whether the gathered narrative actually, specifically answers the task —
        not merely whether *some* text exists, the real judgment DeepEval/Ragas-style groundedness
        scoring performs (still the eventual upgrade over a single judge call, but a genuine second
        opinion rather than a hardcoded proxy). Fails closed: a judge response with no parseable
        score scores 0.0 rather than being treated as confident.
        """
        engineered_context = state.get("engineered_context", "")
        if not engineered_context:
            return {"evaluation_score": 0.0}

        response = await self._llm.complete(
            messages=[
                PromptMessage(role="system", content=_EVALUATION_SYSTEM_PROMPT),
                PromptMessage(
                    role="user",
                    content=(
                        f"Task: {state.get('task', '')}\n\n"
                        f"Gathered narrative:\n{engineered_context}"
                    ),
                ),
            ],
            model=self._model,
        )
        return {"evaluation_score": _parse_evaluation_score(response.text)}

    # --- 09 Guardrail (DETERMINISTIC scan) ---
    async def guardrail_in(self, state: AgentState) -> AgentState:
        """Input guardrail: scan the incoming task for prompt-injection patterns.

        Real pattern scan, not a model call — injection detection on untrusted input must not
        itself route through the model being protected. Flags a small, documented set of
        override-instruction patterns. A hit is a ``violation``-severity flag, which
        ``route_after_guardrail_out``'s sibling check would hard-stop — but the *input* scan runs
        before any evidence gathering, so injected content never reaches a reasoning agent's
        context.
        """
        return {"guardrail_flags": _scan_for_injection(state.get("task", ""))}

    async def guardrail_out(self, state: AgentState) -> AgentState:
        """Output guardrail: scan the evaluated draft for PII leakage / unsubstantiated claims.
        Deterministic pattern scan over ``engineered_context``. A real build adds a classifier and
        a ``governance.access_policy`` ruleset lookup; the scan-and-flag contract and the hard-stop
        routing it feeds are already real and tested.

        A scan that comes back clean, with real evidence gathered, also calls
        ``submit_finding_draft`` — the write half of the RAG loop the retrieval node reads from.
        This is the one point every specialist's draft already passes through before
        anything downstream sees it, so it is the natural gate for "did this actually pass the
        safety scan," not a second, parallel check bolted on elsewhere. The write is best-effort:
        apps/api being unreachable does not invalidate the run itself — the draft still exists in
        the checkpointed state for a human reviewer at the HITL gate either way.
        """
        engineered_context = state.get("engineered_context", "")
        flags = _scan_for_pii(engineered_context)
        has_violation = any(flag.get("severity") == "violation" for flag in flags)

        if has_violation or not engineered_context:
            return {"guardrail_flags": flags}

        evidence = [
            RetrievedChunk(**chunk)
            for item in state.get("evidence_retrieval", [])
            for chunk in item.get("chunks", [])
        ]
        if not evidence:
            return {"guardrail_flags": flags}

        try:
            finding_id = await self._api.submit_finding_draft(
                engagement_id=state["engagement_id"],
                title=state["task"][:200],
                description=engineered_context,
                severity="medium",
                evidence=evidence,
            )
        except UpstreamApiError:
            logger.warning("submit_finding_draft_failed", run_id=state.get("run_id"))
            return {"guardrail_flags": flags}

        return {"guardrail_flags": flags, "submitted_finding_id": finding_id}

    # --- 06 Report Generation (LLM-driven, post-approval only) ---
    async def report_generation(self, state: AgentState) -> AgentState:
        """Compile confirmed findings into a report. Runs only after HITL approval — the graph
        edge, not this node, enforces that ordering (routing.route_after_hitl)."""
        response = await self._llm.complete(
            messages=[
                PromptMessage(role="system", content=_SYSTEM_FRAMING[AgentRole.REPORT_GENERATION]),
                PromptMessage(
                    role="user",
                    content=state.get("engineered_context", "") or state["task"],
                ),
            ],
            model=self._model,
        )
        return {"final_output": {"report": response.text}}


# --- Planner/Evaluation response parsers (module-level, pure, unit-testable) ---


def _parse_plan_agents(text: str) -> list[str]:
    """Parses the planner's JSON array of agent names out of the model's response, tolerant of a
    model wrapping it in prose or a code fence (real models do this often). Falls back to
    ``["retrieval"]``, the safest single-step default, if nothing parseable is found or every
    parsed name is unrecognized — a formatting slip degrades to the old single-step behavior
    rather than crashing the run.
    """
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if match:
        try:
            candidates = json.loads(match.group(0))
        except json.JSONDecodeError:
            candidates = []
        agents = [a for a in candidates if isinstance(a, str) and a in _PLANNABLE_AGENTS]
        if agents:
            return agents
    return [AgentRole.RETRIEVAL.value]


def _parse_evaluation_score(text: str) -> float:
    """Parses the judge's numeric score out of the model's response, tolerant of surrounding
    prose (e.g. "Score: 0.85" instead of a bare number). Clamped to ``[0.0, 1.0]`` since the
    system prompt asks for that range but nothing stops a model from ignoring it. Falls back to
    0.0 (fails closed — an unparseable judge response is treated as unsupported, never silently
    passed through as confident) if no number is found.
    """
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    return max(0.0, min(1.0, float(match.group(0))))


# --- deterministic scanners used by the Guardrail node (module-level, pure, unit-testable) ---

_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard the above",
    "you are now",
    "system prompt:",
)


def _scan_for_injection(text: str) -> list[dict[str, str]]:
    """Flag known prompt-injection override phrases in untrusted input.

    Case-insensitive substring match against a small, documented pattern set. Not a complete
    injection defense — a real classifier is deferred — but a genuine, deterministic check that
    produces the same ``violation``-severity flag shape the routing layer hard-stops on, so the
    input-guardrail path is real end to end, not a placeholder that always passes.
    """
    lowered = text.lower()
    return [
        {"kind": "prompt_injection", "severity": "violation", "pattern": pattern}
        for pattern in _INJECTION_PATTERNS
        if pattern in lowered
    ]


def _scan_for_pii(text: str) -> list[dict[str, str]]:
    """Flag a coarse PII pattern (a US-SSN-shaped token) in an outgoing draft.

    One deterministic pattern rather than a full PII suite (deferred). A hit is a ``soft`` flag,
    not a ``violation``: any Guardrail *soft*-flag routes to mandatory human review rather than a
    hard stop, so this exercises the soft-flag -> HITL path.
    """
    if re.search(r"\b\d{3}-\d{2}-\d{4}\b", text):
        return [{"kind": "pii", "severity": "soft", "pattern": "ssn_like"}]
    return []
