"""Deterministic 3-way match: invoice vs purchase order vs goods received.

Pure functions over plain data so every rule is unit-testable. The AI never decides
whether an invoice matches; it only reads the document (and, later, explains exceptions).
"""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Literal

Status = Literal["pass", "warn", "fail"]

DESCRIPTION_MATCH_THRESHOLD = 0.6


@dataclass
class Check:
    code: str
    status: Status
    message: str
    line_no: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class InvoiceLineIn:
    line_no: int
    sku: str | None
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


@dataclass
class POLineState:
    id: uuid.UUID
    line_no: int
    sku: str | None
    description: str
    unit_price: Decimal
    ordered: Decimal
    received: Decimal
    # Quantity already billed on other invoices that matched or were paid.
    already_invoiced: Decimal


@dataclass
class MatchInput:
    vendor_name_on_invoice: str | None
    vendor_id: uuid.UUID | None
    vendor_wallet: str | None
    vendor_wallet_updated_at: datetime | None
    po_number_on_invoice: str | None
    po_found: bool
    po_vendor_id: uuid.UUID | None
    po_currency: str | None
    invoice_currency: str
    tax: Decimal
    total: Decimal | None
    lines: list[InvoiceLineIn]
    po_lines: list[POLineState]
    duplicate_invoice_ids: list[uuid.UUID] = field(default_factory=list)
    duplicate_file_invoice_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class MatchOutcome:
    outcome: Literal["matched", "exception"]
    checks: list[Check]
    # invoice line_no -> PO line it was paired with
    pairing: dict[int, uuid.UUID]


def _money(x: Decimal) -> str:
    return f"{x:,.2f}"


def _qty(x: Decimal) -> str:
    return f"{x.normalize():f}"


def _pair_line(line: InvoiceLineIn, candidates: list[POLineState]) -> POLineState | None:
    if line.sku:
        for po_line in candidates:
            if po_line.sku and po_line.sku.casefold() == line.sku.casefold():
                return po_line
    best, best_score = None, 0.0
    for po_line in candidates:
        score = SequenceMatcher(
            None, line.description.casefold(), po_line.description.casefold()
        ).ratio()
        if score > best_score:
            best, best_score = po_line, score
    return best if best_score >= DESCRIPTION_MATCH_THRESHOLD else None


def evaluate(
    data: MatchInput,
    *,
    now: datetime,
    price_tolerance: Decimal,
    amount_tolerance: Decimal,
    wallet_change_hold: timedelta,
) -> MatchOutcome:
    checks: list[Check] = []
    pairing: dict[int, uuid.UUID] = {}

    # Header: who and against which PO.
    if data.vendor_id is None:
        checks.append(
            Check(
                "vendor_known",
                "fail",
                f"Vendor '{data.vendor_name_on_invoice or '?'}' is not in the vendor list",
            )
        )
    else:
        checks.append(Check("vendor_known", "pass", "Vendor is in the vendor list"))

    if not data.po_found:
        checks.append(
            Check(
                "po_found",
                "fail",
                f"No purchase order '{data.po_number_on_invoice or '(none on invoice)'}'",
            )
        )
    else:
        checks.append(
            Check("po_found", "pass", f"Purchase order {data.po_number_on_invoice} found")
        )
        if data.vendor_id and data.po_vendor_id != data.vendor_id:
            checks.append(
                Check("po_vendor", "fail", "Purchase order belongs to a different vendor")
            )
        if data.po_currency and data.po_currency != data.invoice_currency:
            checks.append(
                Check(
                    "currency",
                    "fail",
                    f"Invoice is in {data.invoice_currency}, PO is in {data.po_currency}",
                )
            )

    if data.duplicate_invoice_ids:
        checks.append(
            Check(
                "duplicate_number",
                "fail",
                "Another invoice from this vendor has the same invoice number",
            )
        )
    if data.duplicate_file_invoice_ids:
        checks.append(Check("duplicate_file", "fail", "This exact file was already submitted"))

    # Lines: each invoice line must pair with a PO line, at the PO price, within received qty.
    if not data.lines:
        checks.append(Check("lines_present", "fail", "Invoice has no line items"))
    available = list(data.po_lines)
    for line in data.lines:
        po_line = _pair_line(line, available) if data.po_found else None
        if po_line is None:
            if data.po_found:
                checks.append(
                    Check(
                        "line_on_po",
                        "fail",
                        f"'{line.description}' is not on the purchase order",
                        line.line_no,
                    )
                )
            continue
        available.remove(po_line)
        pairing[line.line_no] = po_line.id

        if po_line.unit_price == 0:
            variance = Decimal(0) if line.unit_price == 0 else Decimal(1)
        else:
            variance = (line.unit_price - po_line.unit_price) / po_line.unit_price
        if abs(variance) > price_tolerance:
            checks.append(
                Check(
                    "price",
                    "fail",
                    f"Unit price {_money(line.unit_price)} vs PO {_money(po_line.unit_price)} "
                    f"({variance:+.1%}, tolerance ±{price_tolerance:.0%})",
                    line.line_no,
                )
            )
        else:
            checks.append(Check("price", "pass", "Unit price matches PO", line.line_no))

        billable = po_line.received - po_line.already_invoiced
        if line.quantity > billable:
            message = (
                f"Invoiced {_qty(line.quantity)} but only {_qty(max(billable, Decimal(0)))} "
                f"left to bill ({_qty(po_line.received)} received, "
                f"{_qty(po_line.already_invoiced)} already invoiced)"
                if po_line.already_invoiced
                else f"Invoiced {_qty(line.quantity)} but only {_qty(po_line.received)} received"
            )
            checks.append(Check("quantity", "fail", message, line.line_no))
        else:
            checks.append(
                Check(
                    "quantity",
                    "pass",
                    f"{_qty(line.quantity)} of {_qty(po_line.received)} received",
                    line.line_no,
                )
            )

        if abs(line.quantity * line.unit_price - line.amount) > amount_tolerance:
            checks.append(
                Check(
                    "line_amount",
                    "warn",
                    f"Line amount {_money(line.amount)} ≠ qty × price "
                    f"{_money(line.quantity * line.unit_price)}",
                    line.line_no,
                )
            )

    # Totals.
    lines_total = sum((line.amount for line in data.lines), Decimal(0))
    if data.total is None:
        checks.append(Check("total", "fail", "Invoice total is missing"))
    elif abs(lines_total + data.tax - data.total) > amount_tolerance:
        checks.append(
            Check(
                "total",
                "fail",
                f"Lines {_money(lines_total)} + tax {_money(data.tax)} "
                f"≠ total {_money(data.total)}",
            )
        )
    else:
        checks.append(Check("total", "pass", f"Total {_money(data.total)} adds up"))

    # Where the money would go.
    if data.vendor_id is not None:
        if not data.vendor_wallet:
            checks.append(Check("wallet", "fail", "Vendor has no payout wallet on file"))
        elif (
            data.vendor_wallet_updated_at
            and now - data.vendor_wallet_updated_at < wallet_change_hold
        ):
            hours = int((now - data.vendor_wallet_updated_at).total_seconds() // 3600)
            checks.append(
                Check(
                    "wallet_recently_changed",
                    "warn",
                    f"Payout wallet changed {hours}h ago; confirm with the vendor out-of-band before paying",
                )
            )
        else:
            checks.append(Check("wallet", "pass", "Payout wallet on file"))

    clean = all(c.status == "pass" for c in checks)
    return MatchOutcome("matched" if clean else "exception", checks, pairing)
