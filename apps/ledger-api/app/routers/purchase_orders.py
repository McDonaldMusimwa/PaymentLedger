import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import decisions, workflow
from app.auth import CurrentUser, Principal, require_roles
from app.db import SessionDep
from app.decisions import Actor
from app.models import (
    GoodsReceipt,
    GRNLine,
    Invoice,
    InvoiceStatus,
    POLine,
    PurchaseOrder,
)
from app.routers.vendors import get_vendor
from app.schemas import GoodsReceiptCreate, POCreate, PODetail, POOut

router = APIRouter(prefix="/purchase-orders", tags=["purchase orders"])

Writer = Annotated[Principal, Depends(require_roles("admin", "ap_clerk"))]


async def _get_po(session, org_id: uuid.UUID, po_id: uuid.UUID) -> PurchaseOrder:
    po = await session.get(PurchaseOrder, po_id)
    if po is None or po.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "po_not_found")
    return po


async def _detail(session, po: PurchaseOrder) -> PODetail:
    received = await workflow.received_quantities(session, po)
    detail = PODetail.model_validate(po, from_attributes=True)
    for line in detail.lines:
        line.received = received.get(line.id, 0)
    return detail


@router.get("", response_model=list[POOut])
async def list_pos(principal: CurrentUser, session: SessionDep):
    result = await session.scalars(
        select(PurchaseOrder)
        .where(PurchaseOrder.org_id == principal.org_id)
        .order_by(PurchaseOrder.created_at.desc())
    )
    return result.all()


@router.post("", response_model=PODetail, status_code=status.HTTP_201_CREATED)
async def create_po(body: POCreate, principal: Writer, session: SessionDep):
    vendor = await get_vendor(session, principal.org_id, body.vendor_id)
    po = PurchaseOrder(
        org_id=principal.org_id,
        vendor_id=vendor.id,
        number=body.number.strip(),
        currency=body.currency,
        lines=[POLine(line_no=i, **line.model_dump()) for i, line in enumerate(body.lines, 1)],
    )
    session.add(po)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "po_number_exists") from exc
    await decisions.record(
        session,
        org_id=principal.org_id,
        actor=Actor.user(principal),
        action="po.created",
        summary=f"Raised PO {po.number} to {vendor.name} for {po.total:,.2f} {po.currency}",
        detail={"po_id": po.id},
    )
    await session.commit()
    await session.refresh(po)
    return await _detail(session, po)


@router.get("/{po_id}", response_model=PODetail)
async def get_po(po_id: uuid.UUID, principal: CurrentUser, session: SessionDep):
    return await _detail(session, await _get_po(session, principal.org_id, po_id))


@router.post("/{po_id}/receipts", response_model=PODetail, status_code=status.HTTP_201_CREATED)
async def receive_goods(
    po_id: uuid.UUID, body: GoodsReceiptCreate, principal: Writer, session: SessionDep
):
    po = await _get_po(session, principal.org_id, po_id)
    po_line_ids = {line.id for line in po.lines}
    if any(line.po_line_id not in po_line_ids for line in body.lines):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "line_not_on_po")

    receipt = GoodsReceipt(
        org_id=principal.org_id,
        po_id=po.id,
        note=body.note,
        lines=[GRNLine(**line.model_dump()) for line in body.lines],
    )
    session.add(receipt)
    await session.flush()
    await decisions.record(
        session,
        org_id=principal.org_id,
        actor=Actor.user(principal),
        action="goods.received",
        summary=f"Received goods against PO {po.number}: "
        + ", ".join(f"{line.quantity_received.normalize():f} × line" for line in body.lines),
        detail={
            "po_id": po.id,
            "receipt_id": receipt.id,
            "lines": [line.model_dump() for line in body.lines],
        },
    )

    # New goods can clear quantity exceptions on invoices already waiting on this PO.
    waiting = await session.scalars(
        select(Invoice).where(
            Invoice.po_id == po.id,
            Invoice.status.in_([InvoiceStatus.EXCEPTION, InvoiceStatus.MATCHED]),
        )
    )
    for invoice in waiting.all():
        await workflow.run_match(session, invoice)

    await session.commit()
    await session.refresh(po)
    return await _detail(session, po)
