"""Demo data and simulated settlement, for trying the app before Arc settlement is wired.

Disabled when ENABLE_DEMO=false. Simulated settlements are recorded with chain
"simulated" so they can never be mistaken for onchain payments.
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app import decisions, workflow
from app.auth import Principal, require_roles
from app.config import settings
from app.db import SessionDep
from app.decisions import Actor
from app.models import (
    GoodsReceipt,
    GRNLine,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Payment,
    POLine,
    PurchaseOrder,
    Vendor,
)
from app.schemas import PaymentOut


def require_demo_enabled() -> None:
    if not settings.enable_demo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "demo_disabled")


router = APIRouter(prefix="/demo", tags=["demo"], dependencies=[Depends(require_demo_enabled)])

Admin = Annotated[Principal, Depends(require_roles("admin"))]


def _fake_wallet(name: str) -> str:
    return "0x" + hashlib.sha256(name.encode()).hexdigest()[:40]


D = Decimal

VENDORS = [
    # name, currency, wallet changed hours ago (None = long-standing)
    ("Northwind Packaging", "USDC", None),
    ("Atlas Freight", "USDC", 5),
    ("Helios Office Supply", "EURC", None),
]

# PO number, vendor, currency, [(sku, description, ordered, unit price, received)]
PURCHASE_ORDERS = [
    (
        "PO-1001",
        "Northwind Packaging",
        "USDC",
        [
            ("BOX-L", "Corrugated shipping box, large", D(500), D("1.20"), D(500)),
            ("TAPE-48", "Packing tape 48mm", D(200), D("2.50"), D(200)),
        ],
    ),
    (
        "PO-1002",
        "Northwind Packaging",
        "USDC",
        [
            ("MAIL-P", "Poly mailer 10x13", D(1000), D("0.18"), D(1000)),
        ],
    ),
    (
        "PO-1003",
        "Atlas Freight",
        "USDC",
        [
            ("FRT-PHX", "Freight LA to Phoenix, per pallet", D(10), D("85.00"), D(8)),
        ],
    ),
    (
        "PO-1004",
        "Helios Office Supply",
        "EURC",
        [
            ("CHAIR-E", "Ergonomic office chair", D(6), D("240.00"), D(6)),
            ("DESK-S", "Standing desk", D(3), D("410.00"), D(3)),
        ],
    ),
]

# invoice no, vendor, PO, currency, due in days, terms, [(sku, description, qty, price)], story
INVOICES = [
    (
        "NW-5521",
        "Northwind Packaging",
        "PO-1001",
        "USDC",
        10,
        "2/10 Net 30",
        [
            ("BOX-L", "Corrugated shipping box, large", D(500), D("1.20")),
            ("TAPE-48", "Packing tape 48mm", D(200), D("2.50")),
        ],
        "clean 3-way match",
    ),
    (
        "NW-5530",
        "Northwind Packaging",
        "PO-1002",
        "USDC",
        30,
        "Net 30",
        [
            ("MAIL-P", "Poly mailer 10x13", D(1000), D("0.195")),
        ],
        "unit price 8% above PO",
    ),
    (
        "ATL-889",
        "Atlas Freight",
        "PO-1003",
        "USDC",
        15,
        "Net 15",
        [
            ("FRT-PHX", "Freight LA to Phoenix, per pallet", D(10), D("85.00")),
        ],
        "billed 10 pallets, 8 received; payout wallet changed 5h ago",
    ),
    (
        "NW-5521",
        "Northwind Packaging",
        "PO-1001",
        "USDC",
        10,
        "2/10 Net 30",
        [
            ("BOX-L", "Corrugated shipping box, large", D(500), D("1.20")),
            ("TAPE-48", "Packing tape 48mm", D(200), D("2.50")),
        ],
        "duplicate of NW-5521",
    ),
    (
        "HEL-2026-114",
        "Helios Office Supply",
        "PO-1004",
        "EURC",
        30,
        "Net 30",
        [
            ("CHAIR-E", "Ergonomic office chair", D(6), D("240.00")),
            ("DESK-S", "Standing desk", D(3), D("410.00")),
        ],
        "clean 3-way match in EURC",
    ),
]


@router.post("/seed", status_code=status.HTTP_201_CREATED)
async def seed(principal: Admin, session: SessionDep):
    org = principal.org_id
    if await session.scalar(select(func.count()).select_from(Vendor).where(Vendor.org_id == org)):
        raise HTTPException(status.HTTP_409_CONFLICT, "org_already_has_data")

    actor = Actor.user(principal)
    now = datetime.now(UTC)
    vendors: dict[str, Vendor] = {}
    for name, currency, changed_hours_ago in VENDORS:
        vendor = Vendor(
            org_id=org,
            name=name,
            email=f"billing@{name.split()[0].lower()}.example.com",
            default_currency=currency,
            wallet_address=_fake_wallet(name),
            wallet_updated_at=now - timedelta(hours=changed_hours_ago)
            if changed_hours_ago is not None
            else None,
        )
        session.add(vendor)
        vendors[name] = vendor
    await session.flush()

    for number, vendor_name, currency, lines in PURCHASE_ORDERS:
        po = PurchaseOrder(
            org_id=org,
            vendor_id=vendors[vendor_name].id,
            number=number,
            currency=currency,
            lines=[
                POLine(line_no=i, sku=sku, description=desc, quantity=qty, unit_price=price)
                for i, (sku, desc, qty, price, _) in enumerate(lines, 1)
            ],
        )
        session.add(po)
        await session.flush()
        session.add(
            GoodsReceipt(
                org_id=org,
                po_id=po.id,
                note="Demo receipt",
                lines=[
                    GRNLine(po_line_id=po_line.id, quantity_received=received)
                    for po_line, (*_, received) in zip(po.lines, lines, strict=True)
                ],
            )
        )
    await session.flush()

    created = []
    for number, vendor_name, po_number, currency, due_days, terms, lines, story in INVOICES:
        total = sum((qty * price for *_, qty, price in lines), D(0))
        invoice = Invoice(
            org_id=org,
            vendor_name=vendor_name,
            invoice_number=number,
            po_number=po_number,
            invoice_date=workflow.today(),
            due_date=workflow.today() + timedelta(days=due_days),
            payment_terms=terms,
            currency=currency,
            tax=D(0),
            total=total,
            status=InvoiceStatus.EXTRACTED,
            lines=[
                InvoiceLine(
                    line_no=i,
                    sku=sku,
                    description=desc,
                    quantity=qty,
                    unit_price=price,
                    amount=qty * price,
                )
                for i, (sku, desc, qty, price) in enumerate(lines, 1)
            ],
        )
        session.add(invoice)
        await session.flush()
        await decisions.record(
            session,
            org_id=org,
            invoice_id=invoice.id,
            actor=actor,
            action="invoice.entered",
            summary=f"Demo invoice {number} from {vendor_name} ({story})",
        )
        outcome = await workflow.run_match(session, invoice)
        created.append({"invoice_number": number, "scenario": story, "outcome": outcome.outcome})

    await session.commit()
    return {"vendors": len(vendors), "purchase_orders": len(PURCHASE_ORDERS), "invoices": created}


@router.post("/payments/{payment_id}/simulate-settlement", response_model=PaymentOut)
async def simulate_settlement(payment_id: uuid.UUID, principal: Admin, session: SessionDep):
    payment = await session.get(Payment, payment_id)
    if payment is None or payment.org_id != principal.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "payment_not_found")
    await workflow.settle_payment(
        session,
        payment,
        tx_hash=f"sim-{uuid.uuid4().hex}",
        chain="simulated",
        actor=Actor.user(principal),
    )
    await session.commit()
    return payment
