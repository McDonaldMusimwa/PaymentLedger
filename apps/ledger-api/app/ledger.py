"""Double-entry ledger. Balancing and append-only rules are also enforced by DB triggers."""

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, JournalEntry, Posting

DEFAULT_ACCOUNTS = [
    ("1000", "USDC Treasury (Arc)", "asset"),
    ("1010", "EURC Treasury (Arc)", "asset"),
    ("2000", "Accounts Payable", "liability"),
    ("5000", "Operating Expenses", "expense"),
]
TREASURY_ACCOUNT = {"USDC": "1000", "EURC": "1010"}
ACCOUNTS_PAYABLE = "2000"
OPERATING_EXPENSES = "5000"


class UnbalancedEntryError(ValueError):
    pass


async def ensure_accounts(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Account]:
    await session.execute(
        insert(Account)
        .values(
            [
                {"id": uuid.uuid4(), "org_id": org_id, "code": c, "name": n, "type": t}
                for c, n, t in DEFAULT_ACCOUNTS
            ]
        )
        .on_conflict_do_nothing(index_elements=["org_id", "code"])
    )
    accounts = await session.scalars(select(Account).where(Account.org_id == org_id))
    return {a.code: a for a in accounts}


async def post_entry(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    memo: str,
    source_type: str,
    source_id: uuid.UUID | None,
    idempotency_key: str,
    currency: str,
    lines: list[tuple[str, Decimal]],  # (account code, signed amount: + debit / - credit)
    chain: str | None = None,
    tx_hash: str | None = None,
) -> JournalEntry:
    existing = await session.scalar(
        select(JournalEntry).where(
            JournalEntry.org_id == org_id, JournalEntry.idempotency_key == idempotency_key
        )
    )
    if existing:
        return existing

    if sum(amount for _, amount in lines) != 0:
        raise UnbalancedEntryError(f"entry '{memo}' does not balance")

    accounts = await ensure_accounts(session, org_id)
    entry = JournalEntry(
        org_id=org_id,
        memo=memo,
        source_type=source_type,
        source_id=source_id,
        idempotency_key=idempotency_key,
        chain=chain,
        tx_hash=tx_hash,
        postings=[
            Posting(account_id=accounts[code].id, amount=amount, currency=currency)
            for code, amount in lines
        ],
    )
    session.add(entry)
    await session.flush()
    return entry


async def balances(session: AsyncSession, org_id: uuid.UUID) -> list[dict]:
    await ensure_accounts(session, org_id)
    rows = await session.execute(
        select(Account, Posting.currency, func.coalesce(func.sum(Posting.amount), 0))
        .outerjoin(Posting, Posting.account_id == Account.id)
        .where(Account.org_id == org_id)
        .group_by(Account.id, Posting.currency)
        .order_by(Account.code)
    )
    return [
        {
            "code": account.code,
            "name": account.name,
            "type": account.type,
            "currency": currency,
            # Debit-positive internally; show liabilities/equity/revenue as positive balances.
            "balance": total if account.type in ("asset", "expense") else -total,
        }
        for account, currency, total in rows
    ]
