"""Domain entities for the Identity & Access context.

Plain, framework-free dataclasses — this module imports nothing from SQLAlchemy, FastAPI, or any
other infrastructure concern (domain never depends on infrastructure).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EngagementRole(str, Enum):
    """The five engagement roles this platform's RBAC matrix is built around."""

    AUDITOR = "Auditor"
    FRAUD_ANALYST = "FraudAnalyst"
    COMPLIANCE_MANAGER = "ComplianceManager"
    CAE = "CAE"
    ADMIN = "Admin"


@dataclass(frozen=True)
class User:
    id: str
    entra_object_id: str
    display_name: str
    email: str
    created_at: datetime


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    created_at: datetime


@dataclass(frozen=True)
class Engagement:
    id: str
    tenant_id: str
    name: str
    business_unit: str
    sensitivity_tier: str
    status: str
    created_at: datetime


@dataclass(frozen=True)
class EngagementMembership:
    engagement_id: str
    user_id: str
    role: EngagementRole
    granted_at: datetime


@dataclass(frozen=True)
class EngagementRosterEntry:
    """A membership row joined with its user's profile — the read model Administration's roster
    view needs (a bare :class:`EngagementMembership` has no name to show). Keeping this as its own
    entity rather than reusing ``EngagementMembership`` + a separate ``User`` lookup avoids an N+1
    query per roster row; the repository joins once."""

    user_id: str
    display_name: str
    email: str
    role: EngagementRole
    granted_at: datetime


@dataclass(frozen=True)
class EngagementSummary:
    """A membership row joined with its engagement's own name — the read model the portfolio
    dashboard and the engagement breadcrumb need (a bare :class:`EngagementMembership` has no name
    to show, only an id), the same "join once in the repository" pattern
    :class:`EngagementRosterEntry` already established for the roster view."""

    engagement_id: str
    name: str
    role: EngagementRole
