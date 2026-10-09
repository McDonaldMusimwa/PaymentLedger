import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app import decisions, ledger
from app.auth import CurrentUser
from app.db import SessionDep
from app.models import (
    Decision,
    Invoice,
    InvoiceStatus,
    JournalEntry,
    Payment,
    PaymentStatus,
    Vendor,
)
from app.schemas import AccountBalance, DecisionOut, JournalEntryOut, PaymentOut

router = APIRouter(tags=["ledger"])


@router.get("/ledger/accounts", response_model=list[AccountBalance])
async def account_balances(principal: CurrentUser, session: SessionDep):
    result = await ledger.balances(session, principal.org_id)
    await session.commit()  # ensure_accounts may have created the default chart
    return result


@router.get("/ledger/entries", response_model=list[JournalEntryOut])
async def journal_entries(
    principal: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    result = await session.scalars(
        select(JournalEntry)
        .where(JournalEntry.org_id == principal.org_id)
        .order_by(JournalEntry.created_at.desc())
        .limit(limit)
    )
    return result.all()


@router.get("/payments", response_model=list[PaymentOut])
async def list_payments(
    principal: CurrentUser,
    session: SessionDep,
    status: PaymentStatus | None = None,
):
    query = select(Payment).where(Payment.org_id == principal.org_id)
    if status:
        query = query.where(Payment.status == status)
    result = await session.scalars(query.order_by(Payment.scheduled_for, Payment.created_at))
    return result.all()


@router.get("/decisions", response_model=list[DecisionOut])
async def list_decisions(
    principal: CurrentUser,
    session: SessionDep,
    invoice_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    query = select(Decision).where(Decision.org_id == principal.org_id)
    if invoice_id:
        query = query.where(Decision.invoice_id == invoice_id)
    result = await session.scalars(query.order_by(Decision.seq.desc()).limit(limit))
    return result.all()


@router.get("/decisions/verify")
async def verify_decisions(principal: CurrentUser, session: SessionDep):
    """Recompute the hash chain; any edited or deleted record shows up as broken."""
    return await decisions.verify_chain(session, principal.org_id)


@router.get("/stats")
async def stats(principal: CurrentUser, session: SessionDep):
    org = principal.org_id
    by_status = dict(
        (
            await session.execute(
                select(Invoice.status, func.count())
                .where(Invoice.org_id == org)
                .group_by(Invoice.status)
            )
        ).all()
    )
    paid = (
        await session.execute(
            select(Payment.currency, func.count(), func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.org_id == org, Payment.status == PaymentStatus.SETTLED)
            .group_by(Payment.currency)
        )
    ).all()
    outstanding = (
        await session.execute(
            select(Payment.currency, func.coalesce(func.sum(Payment.amount), 0))
            .where(
                Payment.org_id == org,
                Payment.status.in_([PaymentStatus.PENDING, PaymentStatus.SUBMITTED]),
            )
            .group_by(Payment.currency)
        )
    ).all()
    vendors = await session.scalar(
        select(func.count()).select_from(Vendor).where(Vendor.org_id == org)
    )
    total_invoices = sum(by_status.values())
    auto_matched = await session.scalar(
        select(func.count(func.distinct(Decision.invoice_id))).where(
            Decision.org_id == org, Decision.action == "invoice.matched"
        )
    )
    return {
        "invoices_total": total_invoices,
        "invoices_by_status": {s.value: by_status.get(s, 0) for s in InvoiceStatus},
        "invoices_matched_clean": auto_matched,
        "payments_settled": [{"currency": c, "count": n, "amount": a} for c, n, a in paid],
        "payments_outstanding": [{"currency": c, "amount": a} for c, a in outstanding],
        "vendors": vendors,
    }
