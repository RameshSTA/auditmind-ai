"""Unit tests for the state-channel reducers (Phase 5 §11) — pure functions, no LangGraph import."""

from __future__ import annotations

from agent_orchestrator.domain.state import append_reducer, increment_reducer, overwrite_reducer


def test_append_reducer_concatenates() -> None:
    assert append_reducer(["a"], ["b", "c"]) == ["a", "b", "c"]


def test_append_reducer_treats_none_existing_as_empty() -> None:
    """LangGraph initializes an unset annotated-list channel lazily as None on the first write."""
    assert append_reducer(None, ["first"]) == ["first"]


def test_overwrite_reducer_discards_existing() -> None:
    assert overwrite_reducer("old", "new") == "new"


def test_increment_reducer_adds_to_running_total() -> None:
    assert increment_reducer(2, 1) == 3


def test_increment_reducer_treats_none_existing_as_zero() -> None:
    assert increment_reducer(None, 1) == 1
