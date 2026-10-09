"""Invoice lifecycle: extracted → matched/exception → approved → paid.

Every transition writes to the decision log. Callers own the transaction (commit).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import decisions, ledger
from app.config import settings
from app.decisions import SYSTEM_MATCHER, Actor
from app.extraction import ExtractedInvoice
from app.matching import InvoiceLineIn, MatchInput, MatchOutcome, POLineState, evaluate
from app.models import (
    GoodsReceipt,
    GRNLine,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    MatchResult,
    Payment,
    PaymentStatus,
    PurchaseOrder,
    Vendor,
)

VENDOR_NAME_MATCH_THRESHOLD = 0.85
CURRENCY_ALIASES = {"USD": "USDC", "US$": "USDC", "$": "USDC", "EUR": "EURC", "€": "EURC"}
# Statuses whose line quantities count as already billed against a PO line.
BILLED_STATUSES = (InvoiceStatus.MATCHED, InvoiceStatus.APPROVED, InvoiceStatus.PAID)
MATCHABLE_STATUSES = (InvoiceStatus.EXTRACTED, InvoiceStatus.MATCHED, InvoiceStatus.EXCEPTION)


def fmt_money(x: Decimal | None) -> str:
    return "?" if x is None else f"{x:,.2f}"


def today() -> date:
    return datetime.now(UTC).date()


class WorkflowError(Exception):
    """A request that is valid but not allowed in the invoice's current state."""

    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def normalize_currency(raw: str | None) -> str:
    if not raw:
        return "USDC"
    code = raw.strip().upper()
    return CURRENCY_ALIASES.get(code, code)


def _parse_date(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _dec(x: float | None) -> Decimal | None:
    return None if x is None else Decimal(str(x))


async def _resolve_vendor(
    session: AsyncSession, org_id: uuid.UUID, name: str | None
) -> Vendor | None:
    if not name:
        return None
    vendors = (await session.scalars(select(Vendor).where(Vendor.org_id == org_id))).all()
    wanted = name.casefold().strip()
    for v in vendors:
        if v.name.casefold() == wanted:
            return v
    scored = [(SequenceMatcher(None, wanted, v.name.casefold()).ratio(), v) for v in vendors]
    best = max(scored, default=(0.0, None), key=lambda s: s[0])
    return best[1] if best[0] >= VENDOR_NAME_MATCH_THRESHOLD else None


async def resolve_references(session: AsyncSession, invoice: Invoice) -> None:
    if invoice.vendor_id is None:
        vendor = await _resolve_vendor(session, invoice.org_id, invoice.vendor_name)
        invoice.vendor_id = vendor.id if vendor else None
    if invoice.po_number:
        invoice.po_id = await session.scalar(
            select(PurchaseOrder.id).where(
                PurchaseOrder.org_id == invoice.org_id,
                func.upper(PurchaseOrder.number) == invoice.po_number.strip().upper(),
            )
        )
    else:
        invoice.po_id = None


async def apply_extraction(
    session: AsyncSession, invoice: Invoice, extracted: ExtractedInvoice, model: str
) -> None:
    invoice.extraction = extracted.model_dump()
    invoice.extraction_error = None
    invoice.vendor_name = extracted.vendor_name
    invoice.invoice_number = extracted.invoice_number
    invoice.po_number = extracted.po_number
    invoice.invoice_date = _parse_date(extracted.invoice_date)
    invoice.due_date = _parse_date(extracted.due_date)
    invoice.payment_terms = extracted.payment_terms
    invoice.currency = normalize_currency(extracted.currency)
    invoice.subtotal = _dec(extracted.subtotal)
    invoice.tax = _dec(extracted.tax) or Decimal(0)
    invoice.total = _dec(extracted.total)
    invoice.lines = [
        InvoiceLine(
            line_no=i,
            sku=line.sku,
            description=line.description,
            quantity=Decimal(str(line.quantity)),
            unit_price=Decimal(str(line.unit_price)),
            amount=Decimal(str(line.amount)),
        )
        for i, line in enumerate(extracted.lines, start=1)
    ]
    invoice.status = InvoiceStatus.EXTRACTED
    await resolve_references(session, invoice)
    await session.flush()

    await decisions.record(
        session,
        org_id=invoice.org_id,
        invoice_id=invoice.id,
        actor=Actor("agent", model),
        action="invoice.extracted",
        summary=(
            f"Read invoice {extracted.invoice_number or '?'} from {extracted.vendor_name or '?'}: "
            f"{len(extracted.lines)} lines, total {fmt_money(_dec(extracted.total))} {invoice.currency} "
            f"(confidence {extracted.confidence:.0%})"
        ),
        detail={
            "confidence": extracted.confidence,
            "notes": extracted.notes,
            "is_invoice": extracted.is_invoice,
            "file_sha256": invoice.file_sha256,
        },
    )


async def _po_line_states(
    session: AsyncSession, po: PurchaseOrder, exclude_invoice_id: uuid.UUID
) -> list[POLineState]:
    line_ids = [line.id for line in po.lines]
    received = dict(
        (
            await session.execute(
                select(GRNLine.po_line_id, func.sum(GRNLine.quantity_received))
                .where(GRNLine.po_line_id.in_(line_ids))
                .group_by(GRNLine.po_line_id)
            )
        ).all()
    )
    invoiced = dict(
        (
            await session.execute(
                select(InvoiceLine.po_line_id, func.sum(InvoiceLine.quantity))
                .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
                .where(
                    InvoiceLine.po_line_id.in_(line_ids),
                    Invoice.id != exclude_invoice_id,
                    Invoice.status.in_(BILLED_STATUSES),
                )
                .group_by(InvoiceLine.po_line_id)
            )
        ).all()
    )
    return [
        POLineState(
            id=line.id,
            line_no=line.line_no,
            sku=line.sku,
            description=line.description,
            unit_price=line.unit_price,
            ordered=line.quantity,
            received=received.get(line.id) or Decimal(0),
            already_invoiced=invoiced.get(line.id) or Decimal(0),
        )
        for line in po.lines
    ]


async def run_match(
    session: AsyncSession, invoice: Invoice, actor: Actor = SYSTEM_MATCHER
) -> MatchOutcome:
    if invoice.status not in MATCHABLE_STATUSES:
        raise WorkflowError("not_matchable", f"Cannot match an invoice that is {invoice.status}")

    await resolve_references(session, invoice)
    vendor = await session.get(Vendor, invoice.vendor_id) if invoice.vendor_id else None
    po = None
    if invoice.po_id:
        po = await session.scalar(select(PurchaseOrder).where(PurchaseOrder.id == invoice.po_id))
        await session.refresh(po, ["lines"])

    # Only the later copy is the duplicate: the other invoice must already be billed or
    # have arrived first. Otherwise the original would be blocked by its own duplicate.
    others = select(Invoice.id).where(
        Invoice.org_id == invoice.org_id,
        Invoice.id != invoice.id,
        Invoice.status != InvoiceStatus.REJECTED,
        or_(Invoice.status.in_(BILLED_STATUSES), Invoice.created_at < invoice.created_at),
    )
    duplicate_ids = []
    if invoice.vendor_id and invoice.invoice_number:
        duplicate_ids = (
            await session.scalars(
                others.where(
                    Invoice.vendor_id == invoice.vendor_id,
                    func.upper(Invoice.invoice_number) == invoice.invoice_number.upper(),
                )
            )
        ).all()
    duplicate_file_ids = []
    if invoice.file_sha256:
        duplicate_file_ids = (
            await session.scalars(others.where(Invoice.file_sha256 == invoice.file_sha256))
        ).all()

    outcome = evaluate(
        MatchInput(
            vendor_name_on_invoice=invoice.vendor_name,
            vendor_id=vendor.id if vendor else None,
            vendor_wallet=vendor.wallet_address if vendor else None,
            vendor_wallet_updated_at=vendor.wallet_updated_at if vendor else None,
            po_number_on_invoice=invoice.po_number,
            po_found=po is not None,
            po_vendor_id=po.vendor_id if po else None,
            po_currency=po.currency if po else None,
            invoice_currency=invoice.currency,
            tax=invoice.tax,
            total=invoice.total,
            lines=[
                InvoiceLineIn(
                    line.line_no,
                    line.sku,
                    line.description,
                    line.quantity,
                    line.unit_price,
                    line.amount,
                )
                for line in invoice.lines
            ],
            po_lines=await _po_line_states(session, po, invoice.id) if po else [],
            duplicate_invoice_ids=list(duplicate_ids),
            duplicate_file_invoice_ids=list(duplicate_file_ids),
        ),
        now=datetime.now(UTC),
        price_tolerance=settings.price_tolerance,
        amount_tolerance=settings.amount_tolerance,
        wallet_change_hold=timedelta(hours=settings.wallet_change_hold_hours),
    )

    for line in invoice.lines:
        line.po_line_id = outcome.pairing.get(line.line_no)
    invoice.status = (
        InvoiceStatus.MATCHED if outcome.outcome == "matched" else InvoiceStatus.EXCEPTION
    )
    checks = [c.as_dict() for c in outcome.checks]
    session.add(
        MatchResult(
            org_id=invoice.org_id, invoice_id=invoice.id, outcome=outcome.outcome, checks=checks
        )
    )
    problems = [c for c in outcome.checks if c.status != "pass"]
    await decisions.record(
        session,
        org_id=invoice.org_id,
        invoice_id=invoice.id,
        actor=actor,
        action=f"invoice.{outcome.outcome}",
        summary=(
            "3-way match passed: invoice, PO and goods received agree"
            if not problems
            else f"3-way match exception: {'; '.join(c.message for c in problems)}"
        ),
        detail={"checks": checks},
    )
    await session.flush()
    return outcome


async def approve(
    session: AsyncSession, invoice: Invoice, actor: Actor, override_reason: str | None = None
) -> Payment:
    if invoice.status not in (InvoiceStatus.MATCHED, InvoiceStatus.EXCEPTION):
        raise WorkflowError("not_approvable", f"Cannot approve an invoice that is {invoice.status}")

    # Re-match: another invoice may have billed the same received goods since the last run.
    outcome = await run_match(session, invoice, actor=SYSTEM_MATCHER)
    if outcome.outcome == "exception" and not override_reason:
        raise WorkflowError(
            "match_exception",
            "Invoice has match exceptions; approving it requires an override reason",
            {"checks": [c.as_dict() for c in outcome.checks if c.status != "pass"]},
        )

    vendor = await session.get(Vendor, invoice.vendor_id) if invoice.vendor_id else None
    if vendor is None or not vendor.wallet_address or invoice.total is None:
        raise WorkflowError(
            "not_payable", "Invoice needs a known vendor with a payout wallet and a total"
        )
    if invoice.currency not in ledger.TREASURY_ACCOUNT:
        raise WorkflowError("unsupported_currency", f"Cannot pay in {invoice.currency}")

    await ledger.post_entry(
        session,
        invoice.org_id,
        memo=f"Invoice {invoice.invoice_number} from {vendor.name}",
        source_type="invoice",
        source_id=invoice.id,
        idempotency_key=f"invoice:{invoice.id}:accrual",
        currency=invoice.currency,
        lines=[
            (ledger.OPERATING_EXPENSES, invoice.total),
            (ledger.ACCOUNTS_PAYABLE, -invoice.total),
        ],
    )
    payment = Payment(
        org_id=invoice.org_id,
        invoice_id=invoice.id,
        vendor_id=vendor.id,
        amount=invoice.total,
        currency=invoice.currency,
        to_address=vendor.wallet_address,
        scheduled_for=invoice.due_date or today(),
    )
    session.add(payment)
    invoice.status = InvoiceStatus.APPROVED

    await decisions.record(
        session,
        org_id=invoice.org_id,
        invoice_id=invoice.id,
        actor=actor,
        action="invoice.approved",
        summary=(
            f"Approved {fmt_money(invoice.total)} {invoice.currency} to {vendor.name}, "
            f"payment scheduled for {payment.scheduled_for}"
            + (f". Override: {override_reason}" if override_reason else "")
        ),
        detail={"override_reason": override_reason, "to_address": vendor.wallet_address},
        is_override=bool(override_reason),
    )
    await session.flush()
    return payment


async def reject(session: AsyncSession, invoice: Invoice, actor: Actor, reason: str) -> None:
    if invoice.status in (InvoiceStatus.APPROVED, InvoiceStatus.PAID, InvoiceStatus.REJECTED):
        raise WorkflowError("not_rejectable", f"Cannot reject an invoice that is {invoice.status}")
    invoice.status = InvoiceStatus.REJECTED
    await decisions.record(
        session,
        org_id=invoice.org_id,
        invoice_id=invoice.id,
        actor=actor,
        action="invoice.rejected",
        summary=f"Rejected: {reason}",
        detail={"reason": reason},
    )
    await session.flush()


async def settle_payment(
    session: AsyncSession, payment: Payment, *, tx_hash: str, chain: str, actor: Actor
) -> None:
    if payment.status == PaymentStatus.SETTLED:
        if payment.tx_hash == tx_hash:
            return  # duplicate callback
        raise WorkflowError("already_settled", "Payment already settled with a different tx")
    if payment.status == PaymentStatus.FAILED:
        raise WorkflowError("payment_failed", "Payment is marked failed")

    payment.status = PaymentStatus.SETTLED
    payment.tx_hash = tx_hash
    payment.chain = chain
    payment.settled_at = datetime.now(UTC)
    invoice = await session.get(Invoice, payment.invoice_id)
    invoice.status = InvoiceStatus.PAID

    await ledger.post_entry(
        session,
        payment.org_id,
        memo=f"Paid invoice {invoice.invoice_number} to {payment.vendor.name}",
        source_type="payment",
        source_id=payment.id,
        idempotency_key=f"payment:{payment.id}:settlement",
        currency=payment.currency,
        lines=[
            (ledger.ACCOUNTS_PAYABLE, payment.amount),
            (ledger.TREASURY_ACCOUNT[payment.currency], -payment.amount),
        ],
        chain=chain,
        tx_hash=tx_hash,
    )
    await decisions.record(
        session,
        org_id=payment.org_id,
        invoice_id=invoice.id,
        actor=actor,
        action="payment.settled",
        summary=f"Settled {fmt_money(payment.amount)} {payment.currency} to {payment.to_address} on {chain}",
        detail={
            "tx_hash": tx_hash,
            "chain": chain,
            "payment_id": payment.id,
            "invoice_sha256": invoice.file_sha256,
        },
    )
    await session.flush()


async def fail_payment(
    session: AsyncSession, payment: Payment, *, error: str, actor: Actor
) -> None:
    if payment.status == PaymentStatus.SETTLED:
        raise WorkflowError("already_settled", "Payment already settled")
    payment.status = PaymentStatus.FAILED
    payment.error = error
    await decisions.record(
        session,
        org_id=payment.org_id,
        invoice_id=payment.invoice_id,
        actor=actor,
        action="payment.failed",
        summary=f"Payment failed: {error}",
        detail={"payment_id": payment.id},
    )
    await session.flush()


async def received_quantities(session: AsyncSession, po: PurchaseOrder) -> dict[uuid.UUID, Decimal]:
    rows = await session.execute(
        select(GRNLine.po_line_id, func.sum(GRNLine.quantity_received))
        .join(GoodsReceipt, GoodsReceipt.id == GRNLine.grn_id)
        .where(GoodsReceipt.po_id == po.id)
        .group_by(GRNLine.po_line_id)
    )
    return {line_id: qty for line_id, qty in rows.all()}
