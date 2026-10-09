# PaymentLedger

> **Submission for the [Tameion Agents Hackathon](https://thecanteenapp.com) (Canteen × Circle × Arc), Sep 27 – Oct 17, 2026.**
> Track: **RFB 02 · AP/AR Automation Agent**

An AI agent that runs a business's accounts payable. It reads vendor invoices, checks each one against
the purchase order and the goods actually received (the *3-way match*), decides when and how to pay, and
settles in **USDC on Arc**. Every payment carries proof of what was matched, and every decision the
agent or a person makes goes into an audit trail that can't be edited.

## The problem

Accounts-payable teams still read invoices by hand, compare them line by line with purchase orders and
delivery notes, and pay in batches whenever someone gets round to it. That's slow, and it lets through
the classic AP losses:

- **Duplicate invoices** paid twice
- **Price creep**: billed above the agreed PO price
- **Short shipments**: billed for goods that never arrived
- **Payment-detail fraud**: a "we've changed our bank details" email just before a payment run

A ledger alone doesn't catch any of these: it checks that debits equal credits, not that the vendor,
the amount or the goods were right. PaymentLedger puts those checks in front of every payment, then
settles on Arc, where a payment costs about a cent and lands in under a second. That makes it cheap to
pay each invoice at the best moment instead of in batches.

## How it works

```
Vendor PDF ─▶ AI extraction ─▶ 3-way match ─▶ Approval ─▶ Settlement on Arc ─▶ Ledger + proof
              (Claude)          PO = goods      (agent/     (USDC/EURC via      (append-only,
                                received =      human,      Circle Wallets +    hash-chained
                                invoice         override    policy contract)    decision log)
                                                needs a
                                                reason)
```

1. **Received**: the PDF is checked and its text layer read with pypdf, then stored in Cloudflare R2 at
   `payment-ledger/{org_id}/invoices/{invoice_id}.pdf` with its SHA-256 hash.
2. **Extracted**: an LLM turns the text into structured fields: vendor, PO number, lines, totals, terms.
   A free OpenRouter model handles text PDFs; Claude, if configured, also reads scans.
3. **Matched or exception**: fixed rules (no AI) check vendor, PO, currency, unit price (±2%), quantity
   against goods received minus what's already been billed, totals, duplicates, and the vendor's payout
   wallet. Payments to a wallet changed in the last 72h are held for review. Recording a goods
   receipt re-runs the match for invoices waiting on that PO.
4. **Approved**: posts *Dr Operating Expenses / Cr Accounts Payable* and schedules the payment. Approving
   an invoice with exceptions requires a written override, which is recorded.
5. **Paid**: settled on Arc. Posts *Dr Accounts Payable / Cr USDC Treasury* with the transaction hash.

**Design principle: the AI reads and decides, but it can't break the rules.** The model extracts data and
(soon) chooses payment timing and resolves exceptions. Whether an invoice matches is decided by fixed,
unit-tested rules, and what can actually be paid will be capped by an onchain policy contract. The
database itself rejects edits, deletes and unbalanced journal entries, so nothing, including the agent,
can rewrite the books.

## Status

The hackathon runs until **Oct 17**. This table shows what works today and what doesn't yet.

| Area | Status |
|---|---|
| Auth: organizations, users, roles, Ed25519 JWT + JWKS, rotating refresh tokens | ✅ Working |
| Vendors, purchase orders, goods receipts | ✅ Working |
| Invoice upload: pypdf text extraction, storage in Cloudflare R2 per org | ✅ Working |
| LLM field extraction: free OpenRouter models (text PDFs), Claude optional (scans) | ✅ Working (manual entry if no key) |
| Deterministic 3-way match engine + unit tests | ✅ Working |
| Approvals with audited overrides | ✅ Working |
| Double-entry ledger, append-only and balance-checked in Postgres | ✅ Working |
| Hash-chained decision log + chain verification | ✅ Working |
| React app: inbox, match review, payments, ledger & audit | ✅ Working |
| Settlement on Arc testnet (Circle Wallets, Paymaster) | 🚧 In progress. Payments can currently only be **simulated** and are labelled `simulated` everywhere |
| `PayablesPolicy` contract: vendor allowlist, spend caps, one payment per invoice, onchain proof of the 3 matched documents | 🚧 In progress |
| Agent: payment timing (early-payment discounts vs cash forecast), exception resolution, vendor messages | 🚧 In progress |
| EURC payments via App Kit Swap, cross-chain vendors via CCTP | 📋 Planned |

### Circle / Arc stack

| Tool | Use in PaymentLedger | Status |
|---|---|---|
| USDC & EURC on Arc | Settlement currency per vendor | 🚧 |
| Circle Wallets | Treasury wallet, agent wallet, vendor payout wallets | 🚧 |
| Contracts | `PayablesPolicy`: limits the agent can't exceed, plus proof of payment | 🚧 |
| Paymaster | Vendors never need to hold gas | 📋 |
| App Kit (Swap, Bridge/CCTP) | Pay EUR-invoiced vendors in EURC; vendors on other chains | 📋 |

## Try it

Requires Docker.

```bash
git clone https://github.com/McDonaldMusimwa/PaymentLedger.git
cd PaymentLedger
cp apps/ledger-api/.env.example apps/ledger-api/.env   # optional: R2 + OpenRouter keys
docker compose up -d --build --wait
```

Open **http://localhost:3000**, create an account, and click **Load demo data**. It creates 3 vendors,
4 purchase orders with goods receipts, and 5 invoices covering one scenario each:

| Invoice | Scenario | What the match does |
|---|---|---|
| NW-5521 | Everything agrees | ✅ Matched, ready to approve |
| NW-5530 | Unit price 8% above PO | ❌ Price exception |
| ATL-889 | Billed 10 pallets, 8 received; vendor wallet changed 5h ago | ❌ Quantity + ⚠ wallet hold. Record the 2 missing pallets on PO-1003 and the quantity check clears itself |
| NW-5521 (again) | Duplicate | ❌ Duplicate + already billed |
| HEL-2026-114 | Clean, in EURC | ✅ Matched |

Approve an invoice, settle it (simulated for now), then open **Ledger & audit** to see the journal entries
and the decision log with its hash chain verified.

`./scripts/smoke.sh` runs a quick end-to-end check from the command line.

## Architecture

| Path | Stack | Role |
|---|---|---|
| [`apps/web`](apps/web) | React, Vite, Tailwind, TanStack Query | AP inbox, match review, approvals, payments, ledger & audit |
| [`apps/auth-service`](apps/auth-service) | Node, TypeScript, Fastify | Organizations, users, roles; issues Ed25519 JWTs and publishes `/.well-known/jwks.json` |
| [`apps/ledger-api`](apps/ledger-api) | Python, FastAPI, SQLAlchemy, Alembic, pypdf | Invoices, extraction, matching, approvals, ledger, decision log, and the agent (in progress) |
| [`apps/settlement-service`](apps/settlement-service) | Node, TypeScript | Circle Wallets / App Kit on Arc (in progress) |
| [`contracts`](contracts) | Solidity, Foundry | `PayablesPolicy` (in progress) |
| [`infra/postgres`](infra/postgres) | PostgreSQL 16 | Separate `auth_db` and `ledger_db`, each with its own login |

- The browser talks to one origin; nginx (or Vite in dev) proxies `/auth` and `/api`.
- ledger-api verifies tokens locally against the cached JWKS, so it never calls the auth service per request.
- Every record is scoped to an organization, so each business onboarded is isolated.

Key files:
- [`matching.py`](apps/ledger-api/app/matching.py): the 3-way match rules ([tests](apps/ledger-api/tests/test_matching.py))
- [`extraction.py`](apps/ledger-api/app/extraction.py): LLM extraction (OpenRouter free models or Claude), validated against a strict schema
- [`storage.py`](apps/ledger-api/app/storage.py) / [`pdf.py`](apps/ledger-api/app/pdf.py): R2 storage per org, pypdf validation and text
- [`workflow.py`](apps/ledger-api/app/workflow.py): invoice lifecycle and ledger postings
- [`decisions.py`](apps/ledger-api/app/decisions.py): hash-chained decision log
- [`0002_ap_core.py`](apps/ledger-api/alembic/versions/0002_ap_core.py): database triggers that make the ledger append-only and balanced

## Development

```bash
docker compose up -d postgres
cd apps/auth-service && npm install && npm run dev                      # :4000
cd apps/ledger-api && uv sync && uv run alembic upgrade head \
  && uv run uvicorn app.main:app --reload                                # :8000
cd apps/web && npm install && npm run dev                               # :5173
```

- Tests: `cd apps/ledger-api && uv run pytest`
- New ledger migration: `uv run alembic revision --autogenerate -m "..."`
- Auth migrations are plain SQL files in `apps/auth-service/migrations/`, applied on startup.

## Submission

- **Demo video:** coming before Oct 17
- **Live deployment:** coming before Oct 17
- **Team:** McDonald Musimwa
