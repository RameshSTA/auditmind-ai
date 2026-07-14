"""Domain entities for the Audit Trail context.

Plain, framework-free dataclasses. This context exists to reconcile the SOX/GDPR retention
tension: an append-only record of who (or what) did what, that no application role can ever
update or delete — see the migration's docstring for how that guarantee is actually enforced,
not just documented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class ActorType(str, Enum):
    """An event is attributable to a human, an autonomous agent run, or the system itself
    (e.g. a scheduled job) — never left ambiguous."""

    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


@dataclass(frozen=True)
class AuditEvent:
    id: str
    engagement_id: str
    actor_type: ActorType
    action: str
    subject_type: str
    occurred_at: datetime
    # Polymorphic: a plain id whose meaning is given by `actor_type`/`subject_type`, not a foreign
    # key into one fixed table. `actor_id` is nullable because a `SYSTEM` actor (e.g. a scheduled
    # job) may have no specific user or agent run to attribute the event to.
    actor_id: str | None = None
    subject_id: str | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
