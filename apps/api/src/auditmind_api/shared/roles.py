"""Named role groups for the RBAC permission matrix, used instead of repeating role tuples inline
at every route — makes each route's role restriction a one-line reference back to the matrix
instead of a list that could silently drift out of sync between routes meant to share the same
rule.

Lives in ``shared`` (not e.g. ``reporting``) because every one of reporting/risk/audit_trail/
retrieval/kg/investigations' routes references at least one of these groups — a single shared
home avoids six copies of the same three tuples. Importing ``EngagementRole`` from ``identity``
here is consistent with the "identity is a foundation layer" import-linter contract
(``pyproject.toml``): every other context already depends on identity for
``require_engagement_member`` itself.
"""

from __future__ import annotations

from auditmind_api.identity.domain.entities import EngagementRole

CAN_AUTHOR_FINDINGS = (
    EngagementRole.AUDITOR,
    EngagementRole.FRAUD_ANALYST,
    EngagementRole.COMPLIANCE_MANAGER,
)
CAN_READ_FINDINGS = (
    EngagementRole.AUDITOR,
    EngagementRole.FRAUD_ANALYST,
    EngagementRole.COMPLIANCE_MANAGER,
    EngagementRole.CAE,
)
CAN_DISPOSITION_FINDINGS = (EngagementRole.AUDITOR, EngagementRole.FRAUD_ANALYST)
