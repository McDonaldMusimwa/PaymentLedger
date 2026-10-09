import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import decisions, storage, workflow
from app.auth import CurrentUser, Principal, require_roles
from app.config import settings
from app.db import SessionDep, SessionLocal
from app.decisions import Actor
from app.extraction import ExtractionError, extract_invoice
from app.models import Decision, Invoice, InvoiceLine, InvoiceStatus, MatchResult, Payment
from app.schemas import (
    ApproveRequest,
    InvoiceDetail,
    InvoiceFields,
    InvoiceOut,
    RejectRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])

Writer = Annotated[Principal, Depends(require_roles("admin", "ap_clerk"))]
Approver = Annotated[Principal, Depends(require_roles("admin", "approver"))]

EDITABLE_STATUSES = (
    InvoiceStatus.RECEIVED,
    InvoiceStatus.EXTRACTED,
    InvoiceStatus.MATCHED,
    InvoiceStatus.EXCEPTION,
)


async def _get_invoice(session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    invoice = await session.get(Invoice, invoice_id)
    if invoice is None or invoice.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invoice_not_found")
    return invoice


async def _detail(session: AsyncSession, invoice: Invoice) -> InvoiceDetail:
    # Loads server-generated columns and lines; async sessions can't lazy-load them later.
    await session.refresh(invoice)
    match = await session.scalar(
        select(MatchResult)
        .where(MatchResult.invoice_id == invoice.id)
        .order_by(MatchResult.created_at.desc())
        .limit(1)
    )
    history = await session.scalars(
        select(Decision).where(Decision.invoice_id == invoice.id).order_by(Decision.seq)
    )
    payment = await session.scalar(select(Payment).where(Payment.invoice_id == invoice.id))
    data = {
        name: getattr(invoice, name)
        for name in InvoiceDetail.model_fields
        if hasattr(invoice, name)
    }
    data |= {"match": match, "decisions": history.all(), "payment": payment}
    return InvoiceDetail.model_validate(data, from_attributes=True)


def _apply_fields(invoice: Invoice, body: InvoiceFields) -> None:
    for name in (
        "vendor_name",
        "invoice_number",
        "po_number",
        "invoice_date",
        "due_date",
        "payment_terms",
        "currency",
        "tax",
        "total",
    ):
        setattr(invoice, name, getattr(body, name))
    invoice.vendor_id = None  # re-resolved from vendor_name
    invoice.lines = [
        InvoiceLine(line_no=i, **line.model_dump()) for i, line in enumerate(body.lines, 1)
    ]
    invoice.status = InvoiceStatus.EXTRACTED


async def extract_and_match(invoice_id: uuid.UUID) -> None:
    """Runs after the upload response is sent, in its own session."""
    async with SessionLocal() as session:
        invoice = await session.get(Invoice, invoice_id)
        if invoice is None or invoice.file_key is None:
            return
        try:
            pdf = storage.path_for(invoice.file_key).read_bytes()
            extracted, model = await extract_invoice(pdf)
        except ExtractionError as exc:
            invoice.extraction_error = str(exc)
            await decisions.record(
                session,
                org_id=invoice.org_id,
                invoice_id=invoice.id,
                actor=Actor("system", "extractor"),
                action="invoice.extraction_failed",
                summary=f"Could not read invoice: {exc}. Enter the fields manually.",
            )
            await session.commit()
            return
        except Exception:
            log.exception("extraction crashed for invoice %s", invoice_id)
            invoice.extraction_error = "Unexpected error while reading the invoice"
            await session.commit()
            return

        await workflow.apply_extraction(session, invoice, extracted, model)
        await workflow.run_match(session, invoice)
        await session.commit()


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(
    principal: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
):
    query = select(Invoice).where(Invoice.org_id == principal.org_id)
    if status_filter:
        query = query.where(Invoice.status == status_filter)
    result = await session.scalars(query.order_by(Invoice.created_at.desc()))
    return result.all()


@router.post("", response_model=InvoiceDetail, status_code=status.HTTP_201_CREATED)
async def create_invoice(body: InvoiceFields, principal: Writer, session: SessionDep):
    """Enter an invoice by hand (no file). It is matched immediately."""
    invoice = Invoice(org_id=principal.org_id)
    _apply_fields(invoice, body)
    session.add(invoice)
    await session.flush()
    await decisions.record(
        session,
        org_id=principal.org_id,
        invoice_id=invoice.id,
        actor=Actor.user(principal),
        action="invoice.entered",
        summary=f"Entered invoice {body.invoice_number} from {body.vendor_name} "
        f"for {workflow.fmt_money(body.total)} {body.currency}",
    )
    await workflow.run_match(session, invoice)
    await session.commit()
    return await _detail(session, invoice)


@router.post("/upload", response_model=InvoiceDetail, status_code=status.HTTP_202_ACCEPTED)
async def upload_invoice(
    file: UploadFile, principal: Writer, session: SessionDep, background: BackgroundTasks
):
    """Upload a PDF. Extraction and matching run in the background; poll the invoice."""
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large")
    if not data.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "pdf_required")

    key, sha256 = storage.save(principal.org_id, data)
    invoice = Invoice(
        org_id=principal.org_id,
        file_key=key,
        file_name=Path(file.filename or "invoice.pdf").name[:300],
        file_sha256=sha256,
    )
    session.add(invoice)
    await session.flush()
    await decisions.record(
        session,
        org_id=principal.org_id,
        invoice_id=invoice.id,
        actor=Actor.user(principal),
        action="invoice.received",
        summary=f"Uploaded {invoice.file_name}",
        detail={"file_sha256": sha256, "bytes": len(data)},
    )
    await session.commit()
    background.add_task(extract_and_match, invoice.id)
    return await _detail(session, invoice)


@router.get("/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(invoice_id: uuid.UUID, principal: CurrentUser, session: SessionDep):
    return await _detail(session, await _get_invoice(session, principal.org_id, invoice_id))


@router.get("/{invoice_id}/file")
async def get_invoice_file(invoice_id: uuid.UUID, principal: CurrentUser, session: SessionDep):
    invoice = await _get_invoice(session, principal.org_id, invoice_id)
    if not invoice.file_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no_file")
    return FileResponse(
        storage.path_for(invoice.file_key),
        media_type="application/pdf",
        content_disposition_type="inline",
        filename=invoice.file_name,
    )


@router.put("/{invoice_id}/fields", response_model=InvoiceDetail)
async def correct_fields(
    invoice_id: uuid.UUID, body: InvoiceFields, principal: Writer, session: SessionDep
):
    """Type in or correct the invoice's fields, then re-match."""
    invoice = await _get_invoice(session, principal.org_id, invoice_id)
    if invoice.status not in EDITABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"invoice_is_{invoice.status}")
    _apply_fields(invoice, body)
    await decisions.record(
        session,
        org_id=principal.org_id,
        invoice_id=invoice.id,
        actor=Actor.user(principal),
        action="invoice.corrected",
        summary="Invoice fields entered or corrected by hand",
        detail=body.model_dump(),
    )
    await workflow.run_match(session, invoice)
    await session.commit()
    return await _detail(session, invoice)


@router.post(
    "/{invoice_id}/extract", response_model=InvoiceDetail, status_code=status.HTTP_202_ACCEPTED
)
async def retry_extraction(
    invoice_id: uuid.UUID, principal: Writer, session: SessionDep, background: BackgroundTasks
):
    invoice = await _get_invoice(session, principal.org_id, invoice_id)
    if not invoice.file_key or invoice.status not in EDITABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "not_extractable")
    invoice.extraction_error = None
    await session.commit()
    background.add_task(extract_and_match, invoice.id)
    return await _detail(session, invoice)


@router.post("/{invoice_id}/match", response_model=InvoiceDetail)
async def rematch(invoice_id: uuid.UUID, principal: Writer, session: SessionDep):
    invoice = await _get_invoice(session, principal.org_id, invoice_id)
    await workflow.run_match(session, invoice, actor=Actor.user(principal))
    await session.commit()
    return await _detail(session, invoice)


@router.post("/{invoice_id}/approve", response_model=InvoiceDetail)
async def approve_invoice(
    invoice_id: uuid.UUID, body: ApproveRequest, principal: Approver, session: SessionDep
):
    invoice = await _get_invoice(session, principal.org_id, invoice_id)
    try:
        await workflow.approve(session, invoice, Actor.user(principal), body.override_reason)
    except workflow.WorkflowError:
        # Keep the re-match result and its decision record even though approval was refused.
        await session.commit()
        raise
    await session.commit()
    return await _detail(session, invoice)


@router.post("/{invoice_id}/reject", response_model=InvoiceDetail)
async def reject_invoice(
    invoice_id: uuid.UUID, body: RejectRequest, principal: Approver, session: SessionDep
):
    invoice = await _get_invoice(session, principal.org_id, invoice_id)
    await workflow.reject(session, invoice, Actor.user(principal), body.reason)
    await session.commit()
    return await _detail(session, invoice)
