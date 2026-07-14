"""Repository port for the Audit Trail context.

Deliberately just ``create`` and ``list_for_engagement`` — no ``update`` or ``delete`` method
exists on this port at all. That's not an oversight to fill in later; it's the application-layer
half of the append-only guarantee (the database-level half is the migration's grant set — see
its docstring). A port with no mutation method makes "the audit trail cannot be edited" a property
the type system helps hold, not just a convention every caller has to remember.
"""

from __future__ import annotations

from typing import Protocol

from auditmind_api.audit_trail.domain.entities import AuditEvent


class AuditEventRepository(Protocol):
    async def create(self, event: AuditEvent) -> AuditEvent: ...

    async def list_for_engagement(self, engagement_id: str) -> list[AuditEvent]: ...
