from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import redact, request_id_var
from app.db.models import AuditEvent


def record_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    result: str = "ok",
    entity_type: str | None = None,
    entity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    note: str | None = None,
) -> AuditEvent:
    """Append an audit row in the caller's transaction. Metadata is redacted before storage."""
    rid = request_id_var.get()
    event = AuditEvent(
        actor=actor,
        action=action,
        result=result,
        entity_type=entity_type,
        entity_id=entity_id,
        request_id=None if rid == "-" else rid,
        event_metadata=redact(metadata) if metadata else None,
        note=redact(note) if isinstance(note, str) else note,
    )
    session.add(event)
    return event
