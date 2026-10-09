"""Hash-chained decision log: who decided what, from which inputs, and why."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.models import Decision

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class Actor:
    type: str  # user | system | agent
    id: str

    @classmethod
    def user(cls, principal: Principal) -> "Actor":
        return cls("user", principal.email or str(principal.user_id))


SYSTEM_MATCHER = Actor("system", "matcher")


def _json_safe(value: Any) -> Any:
    # Decimals, dates and UUIDs become strings, so the stored JSONB and the hashed bytes agree.
    return json.loads(json.dumps(value, default=str))


def _digest(prev_hash: str, d: Decision) -> str:
    payload = {
        "seq": d.seq,
        "org_id": str(d.org_id),
        "invoice_id": str(d.invoice_id) if d.invoice_id else None,
        "actor_type": d.actor_type,
        "actor_id": d.actor_id,
        "action": d.action,
        "summary": d.summary,
        "detail": d.detail,
        "is_override": d.is_override,
        "created_at": d.created_at.astimezone(UTC).isoformat(),
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


async def record(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor: Actor,
    action: str,
    summary: str,
    invoice_id: uuid.UUID | None = None,
    detail: dict | None = None,
    is_override: bool = False,
) -> Decision:
    # Serialize appends per org so two concurrent decisions can't claim the same seq/prev_hash.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"decisions:{org_id}"}
    )
    last = await session.scalar(
        select(Decision).where(Decision.org_id == org_id).order_by(Decision.seq.desc()).limit(1)
    )
    prev_hash = last.hash if last else GENESIS_HASH
    decision = Decision(
        org_id=org_id,
        seq=(last.seq + 1) if last else 1,
        invoice_id=invoice_id,
        actor_type=actor.type,
        actor_id=actor.id,
        action=action,
        summary=summary,
        detail=_json_safe(detail or {}),
        is_override=is_override,
        created_at=datetime.now(UTC),
        prev_hash=prev_hash,
    )
    decision.hash = _digest(prev_hash, decision)
    session.add(decision)
    await session.flush()
    return decision


async def verify_chain(session: AsyncSession, org_id: uuid.UUID) -> dict:
    rows = await session.scalars(
        select(Decision).where(Decision.org_id == org_id).order_by(Decision.seq)
    )
    prev_hash = GENESIS_HASH
    count = 0
    for d in rows:
        count += 1
        if d.prev_hash != prev_hash or _digest(prev_hash, d) != d.hash:
            return {"valid": False, "count": count, "broken_at_seq": d.seq}
        prev_hash = d.hash
    return {"valid": True, "count": count, "broken_at_seq": None, "head": prev_hash}
