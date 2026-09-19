"""Where the API keeps what it has been told.

**In memory, deliberately.** §13 names no database and §13.4 is explicit that
the hub's physical steps are somebody else's: "the API only receives their
events". Choosing Postgres here would be answering a question nobody has asked
and committing the operation to a migration story before it has a schema owner.
The repositories are small and behind one object, so the day a store is chosen
it is replaced here rather than in twenty handlers.

What that costs is honest and worth stating: restart the process and the day is
gone. Nothing here is durable, and a deployment that needs it to be needs this
module rewritten first.

§13.1's replay records used to live here and no longer do. They had to survive
a restart and be shared between API containers, and `ddn.api.idempotency` keeps
them in the queue's Redis for both reasons.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """§13.1: "Overrides (§8.2) and outcome events record the actor and time"."""

    at: datetime
    actor: str
    action: str
    subject: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Store:
    """The API's state. One object so that replacing it is one change."""

    envelopes: dict[str, dict[str, Any]] = field(default_factory=dict)
    mailbags: dict[str, dict[str, Any]] = field(default_factory=dict)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: §9.1 transfer requests, by transfer_id (§5.3.2).
    transfers: dict[str, dict[str, Any]] = field(default_factory=dict)
    plans: dict[str, dict[str, Any]] = field(default_factory=dict)
    pickup_plan: dict[str, Any] = field(default_factory=dict)
    audit: list[AuditRecord] = field(default_factory=list)

    # ---- §13.1's audit
    def record(self, *, actor: str, action: str, subject: str,
               at: datetime | None = None, **detail: Any) -> AuditRecord:
        entry = AuditRecord(at=at or datetime.now(UTC), actor=actor,
                            action=action, subject=subject, detail=detail)
        self.audit.append(entry)
        return entry

    def audit_for(self, subject: str) -> Iterator[AuditRecord]:
        return (entry for entry in self.audit if entry.subject == subject)

    # ---- the pools the handlers read
    def ready_at(self, facility_id: str, by: datetime) -> int:
        """§5.2.5: how many will be ready at a facility by a given moment."""
        return sum(
            1 for envelope in self.envelopes.values()
            if envelope.get("facility_id") == facility_id
            and envelope.get("expected_ready_at") is not None
            and envelope["expected_ready_at"] <= by)
