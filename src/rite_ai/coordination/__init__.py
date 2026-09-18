"""Cross-machine coordination (SPEC §2.4, §3.3) — Phase 2.

Nothing here is reachable from Phase 1: a single-Manager setup is trivially
Owner and never runs an election (§2.4's own warning).
"""

from rite_ai.coordination.schemas import (
    LeaseVerdict,
    ManagerStatus,
    OwnerLease,
    lease_from_json,
    lease_to_json,
    status_from_json,
    status_to_json,
)

__all__ = [
    "LeaseVerdict",
    "ManagerStatus",
    "OwnerLease",
    "lease_from_json",
    "lease_to_json",
    "status_from_json",
    "status_to_json",
]
