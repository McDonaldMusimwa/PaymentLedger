import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

Currency = Literal["USDC", "EURC"]
PositiveQty = Field(gt=0, max_digits=18, decimal_places=4)
NonNegMoney = Field(ge=0, max_digits=20, decimal_places=6)
# EVM address (Arc is EVM-compatible).
WalletAddress = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{40}$")]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# Vendors


class VendorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    default_currency: Currency = "USDC"
    wallet_address: WalletAddress | None = None


class VendorUpdate(BaseModel):
    email: EmailStr | None = None
    default_currency: Currency | None = None
    wallet_address: WalletAddress | None = None


class VendorOut(ORM):
    id: uuid.UUID
    name: str
    email: str | None
    default_currency: str
    wallet_address: str | None
    wallet_updated_at: datetime | None
    created_at: datetime


# Purchase orders and goods receipts


class POLineIn(BaseModel):
    sku: str | None = Field(default=None, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = PositiveQty
    unit_price: Decimal = NonNegMoney


class POCreate(BaseModel):
    vendor_id: uuid.UUID
    number: str = Field(min_length=1, max_length=64)
    currency: Currency = "USDC"
    lines: list[POLineIn] = Field(min_length=1)


class POLineOut(ORM):
    id: uuid.UUID
    line_no: int
    sku: str | None
    description: str
    quantity: Decimal
    unit_price: Decimal
    received: Decimal = Decimal(0)


class POOut(ORM):
    id: uuid.UUID
    number: str
    currency: str
    status: str
    vendor_id: uuid.UUID
    vendor_name: str
    total: Decimal
    created_at: datetime


class PODetail(POOut):
    lines: list[POLineOut]
    receipts: list["GoodsReceiptOut"]


class GRNLineIn(BaseModel):
    po_line_id: uuid.UUID
    quantity_received: Decimal = PositiveQty


class GoodsReceiptCreate(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
    lines: list[GRNLineIn] = Field(min_length=1)


class GRNLineOut(ORM):
    po_line_id: uuid.UUID
    quantity_received: Decimal


class GoodsReceiptOut(ORM):
    id: uuid.UUID
    received_at: datetime
    note: str | None
    lines: list[GRNLineOut]


# Invoices


class InvoiceLineIn(BaseModel):
    sku: str | None = Field(default=None, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = PositiveQty
    unit_price: Decimal = NonNegMoney
    amount: Decimal = NonNegMoney


class InvoiceFields(BaseModel):
    """Invoice data typed in by a person (or corrected after extraction)."""

    vendor_name: str = Field(min_length=1, max_length=200)
    invoice_number: str = Field(min_length=1, max_length=64)
    po_number: str | None = Field(default=None, max_length=64)
    invoice_date: date | None = None
    due_date: date | None = None
    payment_terms: str | None = Field(default=None, max_length=100)
    currency: Currency = "USDC"
    tax: Decimal = NonNegMoney
    total: Decimal = NonNegMoney
    lines: list[InvoiceLineIn] = Field(min_length=1)


class InvoiceLineOut(ORM):
    line_no: int
    sku: str | None
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    po_line_id: uuid.UUID | None


class InvoiceOut(ORM):
    id: uuid.UUID
    status: str
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    invoice_number: str | None
    po_number: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str
    total: Decimal | None
    file_name: str | None
    extraction_error: str | None
    created_at: datetime


class MatchResultOut(ORM):
    outcome: str
    checks: list[dict]
    created_at: datetime


class DecisionOut(ORM):
    seq: int
    invoice_id: uuid.UUID | None
    actor_type: str
    actor_id: str
    action: str
    summary: str
    detail: dict
    is_override: bool
    created_at: datetime
    prev_hash: str
    hash: str


class PaymentOut(ORM):
    id: uuid.UUID
    invoice_id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_name: str
    amount: Decimal
    currency: str
    to_address: str
    status: str
    scheduled_for: date
    chain: str | None
    tx_hash: str | None
    error: str | None
    created_at: datetime
    settled_at: datetime | None


class InvoiceDetail(InvoiceOut):
    payment_terms: str | None
    subtotal: Decimal | None
    tax: Decimal
    po_id: uuid.UUID | None
    file_sha256: str | None
    extraction: dict | None
    lines: list[InvoiceLineOut]
    match: MatchResultOut | None
    decisions: list[DecisionOut]
    payment: PaymentOut | None


class ApproveRequest(BaseModel):
    override_reason: str | None = Field(default=None, min_length=5, max_length=1000)


class RejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


# Ledger


class PostingOut(ORM):
    account_code: str
    account_name: str
    amount: Decimal
    currency: str


class JournalEntryOut(ORM):
    id: uuid.UUID
    memo: str
    source_type: str
    source_id: uuid.UUID | None
    chain: str | None
    tx_hash: str | None
    created_at: datetime
    postings: list[PostingOut]


class AccountBalance(BaseModel):
    code: str
    name: str
    type: str
    currency: str | None
    balance: Decimal


# Settlement callbacks (settlement-service → ledger-api)


class SettlementResult(BaseModel):
    tx_hash: str = Field(min_length=1, max_length=100)
    chain: str = Field(default="arc-testnet", max_length=32)


class SettlementFailure(BaseModel):
    error: str = Field(min_length=1, max_length=2000)
