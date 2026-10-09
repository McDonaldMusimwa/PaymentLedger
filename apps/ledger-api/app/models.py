import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# USDC and EURC both have 6 decimals onchain.
Money = Numeric(20, 6)
Quantity = Numeric(18, 4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class InvoiceStatus(StrEnum):
    RECEIVED = "received"  # file stored, not yet read
    EXTRACTED = "extracted"  # fields known, not yet matched
    MATCHED = "matched"  # 3-way match passed
    EXCEPTION = "exception"  # 3-way match failed or needs review
    APPROVED = "approved"  # accrued in the ledger, payment queued
    REJECTED = "rejected"
    PAID = "paid"


class PaymentStatus(StrEnum):
    PENDING = "pending"  # waiting for the settlement-service
    SUBMITTED = "submitted"  # transaction sent, not yet confirmed
    SETTLED = "settled"
    FAILED = "failed"


class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Organization (business) from the auth-service token; every query filters on it.
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    default_currency: Mapped[str] = mapped_column(String(4), default="USDC")
    wallet_address: Mapped[str | None] = mapped_column(String(64))
    # A recent wallet change is the classic invoice-fraud signal; payments to it are held.
    wallet_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("org_id", "number"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"))
    number: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = _created_at()

    vendor: Mapped[Vendor] = relationship(lazy="joined")
    lines: Mapped[list["POLine"]] = relationship(
        back_populates="po",
        order_by="POLine.line_no",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    receipts: Mapped[list["GoodsReceipt"]] = relationship(
        back_populates="po", order_by="GoodsReceipt.received_at", lazy="selectin"
    )

    @property
    def vendor_name(self) -> str:
        return self.vendor.name

    @property
    def total(self) -> Decimal:
        return sum((line.quantity * line.unit_price for line in self.lines), Decimal(0))


class POLine(Base):
    __tablename__ = "po_lines"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    unit_price: Mapped[Decimal] = mapped_column(Money)

    po: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    po_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id"))
    received_at: Mapped[datetime] = _created_at()
    note: Mapped[str | None] = mapped_column(Text)

    po: Mapped[PurchaseOrder] = relationship(back_populates="receipts")
    lines: Mapped[list["GRNLine"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class GRNLine(Base):
    __tablename__ = "grn_lines"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    grn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("goods_receipts.id", ondelete="CASCADE"))
    po_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("po_lines.id"))
    quantity_received: Mapped[Decimal] = mapped_column(Quantity)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(16), default=InvoiceStatus.RECEIVED, index=True)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id"))
    po_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_orders.id"))

    # As printed on the invoice (extracted or typed in); vendor_id/po_id are what they resolved to.
    vendor_name: Mapped[str | None] = mapped_column(String(200))
    invoice_number: Mapped[str | None] = mapped_column(String(64))
    po_number: Mapped[str | None] = mapped_column(String(64))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    payment_terms: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(4), default="USDC")
    subtotal: Mapped[Decimal | None] = mapped_column(Money)
    tax: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    total: Mapped[Decimal | None] = mapped_column(Money)

    file_key: Mapped[str | None] = mapped_column(String(300))
    file_name: Mapped[str | None] = mapped_column(String(300))
    file_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    page_count: Mapped[int | None] = mapped_column(Integer)
    # Text layer read by pypdf; empty for scanned PDFs. Kept for search and re-extraction.
    pdf_text: Mapped[str | None] = mapped_column(Text)
    extraction: Mapped[dict | None] = mapped_column(JSONB)
    extraction_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    vendor: Mapped[Vendor | None] = relationship(lazy="joined")
    lines: Mapped[list["InvoiceLine"]] = relationship(
        order_by="InvoiceLine.line_no", cascade="all, delete-orphan", lazy="selectin"
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    amount: Mapped[Decimal] = mapped_column(Money)
    # Set by the matcher to the PO line this invoice line was paired with.
    po_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("po_lines.id"))


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(16))  # matched | exception
    checks: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("org_id", "code"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16))  # asset | liability | equity | revenue | expense


class JournalEntry(Base):
    """Append-only (enforced by trigger). Corrections are new, reversing entries."""

    __tablename__ = "journal_entries"
    __table_args__ = (UniqueConstraint("org_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    memo: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[uuid.UUID | None]
    # A retry with the same key returns the existing entry instead of posting twice.
    idempotency_key: Mapped[str] = mapped_column(String(200))
    chain: Mapped[str | None] = mapped_column(String(32))
    tx_hash: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _created_at()

    postings: Mapped[list["Posting"]] = relationship(lazy="selectin")


class Posting(Base):
    """Signed amount: positive = debit, negative = credit. Each entry sums to zero per currency."""

    __tablename__ = "postings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journal_entries.id"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(4))

    account: Mapped[Account] = relationship(lazy="joined")

    @property
    def account_code(self) -> str:
        return self.account.code

    @property
    def account_name(self) -> str:
        return self.account.name


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), unique=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"))
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(4))
    to_address: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=PaymentStatus.PENDING, index=True)
    scheduled_for: Mapped[date] = mapped_column(Date)
    chain: Mapped[str | None] = mapped_column(String(32))
    tx_hash: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vendor: Mapped[Vendor] = relationship(lazy="joined")

    @property
    def vendor_name(self) -> str:
        return self.vendor.name


class Decision(Base):
    """Append-only, hash-chained record of every decision (human, system, or agent).

    Each row's hash covers its content and the previous row's hash for the same org, so
    any edit or deletion breaks the chain from that point on.
    """

    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("org_id", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    seq: Mapped[int] = mapped_column(Integer)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # user | system | agent
    actor_id: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    is_override: Mapped[bool] = mapped_column(Boolean, default=False)
