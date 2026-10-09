"""Service-to-service endpoints for the settlement-service. Not for browsers."""

import hmac
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select

from app import workflow
from app.config import settings
from app.db import SessionDep
from app.decisions import Actor
from app.models import Payment, PaymentStatus
from app.schemas import PaymentOut, SettlementFailure, SettlementResult

SETTLEMENT_SERVICE = Actor("system", "settlement-service")


def require_internal_token(x_internal_token: Annotated[str, Header()]) -> None:
    if not hmac.compare_digest(x_internal_token, settings.internal_api_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_internal_token")


router = APIRouter(
    prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_token)]
)


async def _get_payment(session, payment_id: uuid.UUID) -> Payment:
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "payment_not_found")
    return payment


@router.get("/payments/due", response_model=list[PaymentOut])
async def due_payments(session: SessionDep, on_or_before: date | None = None):
    """Pending payments whose scheduled date has arrived, across all orgs."""
    result = await session.scalars(
        select(Payment)
        .where(
            Payment.status == PaymentStatus.PENDING,
            Payment.scheduled_for <= (on_or_before or workflow.today()),
        )
        .order_by(Payment.scheduled_for)
    )
    return result.all()


@router.post("/payments/{payment_id}/submitted", response_model=PaymentOut)
async def payment_submitted(payment_id: uuid.UUID, body: SettlementResult, session: SessionDep):
    payment = await _get_payment(session, payment_id)
    if payment.status == PaymentStatus.PENDING:
        payment.status = PaymentStatus.SUBMITTED
        payment.tx_hash = body.tx_hash
        payment.chain = body.chain
        await session.commit()
    return payment


@router.post("/payments/{payment_id}/settled", response_model=PaymentOut)
async def payment_settled(payment_id: uuid.UUID, body: SettlementResult, session: SessionDep):
    payment = await _get_payment(session, payment_id)
    await workflow.settle_payment(
        session, payment, tx_hash=body.tx_hash, chain=body.chain, actor=SETTLEMENT_SERVICE
    )
    await session.commit()
    return payment


@router.post("/payments/{payment_id}/failed", response_model=PaymentOut)
async def payment_failed(payment_id: uuid.UUID, body: SettlementFailure, session: SessionDep):
    payment = await _get_payment(session, payment_id)
    await workflow.fail_payment(session, payment, error=body.error, actor=SETTLEMENT_SERVICE)
    await session.commit()
    return payment
