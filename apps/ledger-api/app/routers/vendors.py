import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import decisions
from app.auth import CurrentUser, Principal, require_roles
from app.db import SessionDep
from app.decisions import Actor
from app.models import Vendor
from app.schemas import VendorCreate, VendorOut, VendorUpdate

router = APIRouter(prefix="/vendors", tags=["vendors"])

Writer = Annotated[Principal, Depends(require_roles("admin", "ap_clerk"))]


async def get_vendor(session, org_id: uuid.UUID, vendor_id: uuid.UUID) -> Vendor:
    vendor = await session.get(Vendor, vendor_id)
    if vendor is None or vendor.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "vendor_not_found")
    return vendor


@router.get("", response_model=list[VendorOut])
async def list_vendors(principal: CurrentUser, session: SessionDep):
    result = await session.scalars(
        select(Vendor).where(Vendor.org_id == principal.org_id).order_by(Vendor.name)
    )
    return result.all()


@router.post("", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
async def create_vendor(body: VendorCreate, principal: Writer, session: SessionDep):
    vendor = Vendor(org_id=principal.org_id, **body.model_dump())
    session.add(vendor)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "vendor_exists") from exc
    await decisions.record(
        session,
        org_id=principal.org_id,
        actor=Actor.user(principal),
        action="vendor.created",
        summary=f"Added vendor {vendor.name}",
        detail={"vendor_id": vendor.id, "wallet_address": vendor.wallet_address},
    )
    await session.commit()
    await session.refresh(vendor)
    return vendor


@router.patch("/{vendor_id}", response_model=VendorOut)
async def update_vendor(
    vendor_id: uuid.UUID, body: VendorUpdate, principal: Writer, session: SessionDep
):
    vendor = await get_vendor(session, principal.org_id, vendor_id)
    changes = body.model_dump(exclude_unset=True)
    old_wallet = vendor.wallet_address
    for field, value in changes.items():
        setattr(vendor, field, value)

    if "wallet_address" in changes and changes["wallet_address"] != old_wallet:
        # Starts the hold window: the matcher flags payments to this wallet for a while.
        vendor.wallet_updated_at = datetime.now(UTC)
        await decisions.record(
            session,
            org_id=principal.org_id,
            actor=Actor.user(principal),
            action="vendor.wallet_changed",
            summary=f"Payout wallet for {vendor.name} changed from {old_wallet or 'none'} "
            f"to {vendor.wallet_address or 'none'}",
            detail={"vendor_id": vendor.id, "old": old_wallet, "new": vendor.wallet_address},
        )
    await session.commit()
    await session.refresh(vendor)
    return vendor
