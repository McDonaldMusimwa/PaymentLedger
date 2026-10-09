import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from app.matching import InvoiceLineIn, MatchInput, POLineState, evaluate

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
VENDOR = uuid.uuid4()
BOX = uuid.uuid4()
TAPE = uuid.uuid4()


def po_lines(received_box=D(500), invoiced_box=D(0)):
    return [
        POLineState(
            BOX,
            1,
            "BOX-L",
            "Corrugated shipping box, large",
            D("1.20"),
            D(500),
            received_box,
            invoiced_box,
        ),
        POLineState(TAPE, 2, "TAPE-48", "Packing tape 48mm", D("2.50"), D(200), D(200), D(0)),
    ]


def make_input(**overrides) -> MatchInput:
    base = MatchInput(
        vendor_name_on_invoice="Northwind Packaging",
        vendor_id=VENDOR,
        vendor_wallet="0x" + "a" * 40,
        vendor_wallet_updated_at=None,
        po_number_on_invoice="PO-1001",
        po_found=True,
        po_vendor_id=VENDOR,
        po_currency="USDC",
        invoice_currency="USDC",
        tax=D(0),
        total=D("1100.00"),
        lines=[
            InvoiceLineIn(1, "BOX-L", "Corrugated shipping box, large", D(500), D("1.20"), D(600)),
            InvoiceLineIn(2, "TAPE-48", "Packing tape 48mm", D(200), D("2.50"), D(500)),
        ],
        po_lines=po_lines(),
    )
    return replace(base, **overrides)


def run(data: MatchInput):
    return evaluate(
        data,
        now=NOW,
        price_tolerance=D("0.02"),
        amount_tolerance=D("0.01"),
        wallet_change_hold=timedelta(hours=72),
    )


def failing(outcome) -> set[str]:
    return {c.code for c in outcome.checks if c.status != "pass"}


def test_clean_match():
    outcome = run(make_input())
    assert outcome.outcome == "matched", failing(outcome)
    assert outcome.pairing == {1: BOX, 2: TAPE}


def test_price_over_tolerance():
    data = make_input(
        lines=[
            InvoiceLineIn(1, "BOX-L", "box", D(500), D("1.30"), D(650)),
            InvoiceLineIn(2, "TAPE-48", "tape", D(200), D("2.50"), D(500)),
        ],
        total=D(1150),
    )
    assert failing(run(data)) == {"price"}


def test_price_within_tolerance():
    data = make_input(
        lines=[
            InvoiceLineIn(1, "BOX-L", "box", D(500), D("1.22"), D(610)),
            InvoiceLineIn(2, "TAPE-48", "tape", D(200), D("2.50"), D(500)),
        ],
        total=D(1110),
    )
    assert run(data).outcome == "matched"


def test_short_shipment():
    assert failing(run(make_input(po_lines=po_lines(received_box=D(400))))) == {"quantity"}


def test_already_invoiced_counts_against_received():
    outcome = run(make_input(po_lines=po_lines(invoiced_box=D(500))))
    assert failing(outcome) == {"quantity"}
    assert "already invoiced" in next(c.message for c in outcome.checks if c.code == "quantity")


def test_description_pairing_without_sku():
    data = make_input(
        lines=[
            InvoiceLineIn(1, None, "Corrugated shipping boxes - large", D(500), D("1.20"), D(600)),
            InvoiceLineIn(2, None, "Packing tape, 48mm", D(200), D("2.50"), D(500)),
        ]
    )
    assert run(data).pairing == {1: BOX, 2: TAPE}


def test_line_not_on_po():
    data = make_input(
        lines=[
            *make_input().lines,
            InvoiceLineIn(3, None, "Expedited handling fee", D(1), D(75), D(75)),
        ],
        total=D(1175),
    )
    assert failing(run(data)) == {"line_on_po"}


def test_totals_must_add_up():
    assert failing(run(make_input(total=D(1200)))) == {"total"}


def test_unknown_vendor_and_missing_po():
    data = make_input(
        vendor_id=None, po_found=False, po_vendor_id=None, po_currency=None, po_lines=[]
    )
    assert {"vendor_known", "po_found"} <= failing(run(data))


def test_po_from_other_vendor():
    assert "po_vendor" in failing(run(make_input(po_vendor_id=uuid.uuid4())))


def test_currency_mismatch():
    assert "currency" in failing(run(make_input(invoice_currency="EURC")))


def test_duplicates():
    data = make_input(
        duplicate_invoice_ids=[uuid.uuid4()], duplicate_file_invoice_ids=[uuid.uuid4()]
    )
    assert failing(run(data)) == {"duplicate_number", "duplicate_file"}


def test_recent_wallet_change_is_held_for_review():
    outcome = run(make_input(vendor_wallet_updated_at=NOW - timedelta(hours=5)))
    assert outcome.outcome == "exception"
    assert [c.status for c in outcome.checks if c.code == "wallet_recently_changed"] == ["warn"]


def test_old_wallet_change_is_fine():
    assert run(make_input(vendor_wallet_updated_at=NOW - timedelta(days=10))).outcome == "matched"


def test_no_wallet():
    assert "wallet" in failing(run(make_input(vendor_wallet=None)))
