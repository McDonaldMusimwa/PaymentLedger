import asyncio
import json
from types import SimpleNamespace

import pytest

from app import extraction
from app.extraction import INVOICE_SCHEMA, ExtractionError

VALID = {
    "is_invoice": True,
    "vendor_name": "Helios Office Supply",
    "invoice_number": "HEL-1",
    "po_number": "PO-1004",
    "invoice_date": "2026-10-09",
    "due_date": None,
    "payment_terms": "Net 30",
    "currency": "EUR",
    "subtotal": 480.0,
    "tax": 0.0,
    "total": 480.0,
    "lines": [
        {"sku": "CHAIR-E", "description": "Chair", "quantity": 2, "unit_price": 240, "amount": 480}
    ],
    "confidence": 0.9,
    "notes": "",
}


class FakeCompletions:
    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.replies[len(self.calls) - 1])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], model="test/free"
        )


@pytest.fixture
def fake_openrouter(monkeypatch):
    def install(replies: list[str]) -> FakeCompletions:
        completions = FakeCompletions(replies)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        monkeypatch.setattr(extraction, "_openrouter", lambda: client)
        monkeypatch.setattr(extraction.settings, "extraction_provider", "openrouter")
        return completions

    return install


def run(coro):
    return asyncio.run(coro)


def test_schema_is_strict_everywhere():
    line = INVOICE_SCHEMA["$defs"]["ExtractedLine"]
    for obj in (INVOICE_SCHEMA, line):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_reads_text_with_openrouter(fake_openrouter):
    calls = fake_openrouter([json.dumps(VALID)])
    invoice, model = run(extraction.extract_invoice(b"%PDF", text="INVOICE HEL-1 ..."))
    assert invoice.invoice_number == "HEL-1"
    assert model == "openrouter:test/free"
    assert "<invoice_text>" in calls.calls[0]["messages"][1]["content"]


def test_accepts_markdown_fenced_json(fake_openrouter):
    fake_openrouter([f"```json\n{json.dumps(VALID)}\n```"])
    invoice, _ = run(extraction.extract_invoice(b"%PDF", text="INVOICE"))
    assert invoice.total == 480.0


def test_retries_once_on_schema_mismatch(fake_openrouter):
    calls = fake_openrouter(['{"vendor_name": "Helios"}', json.dumps(VALID)])
    invoice, _ = run(extraction.extract_invoice(b"%PDF", text="INVOICE"))
    assert invoice.vendor_name == "Helios Office Supply"
    assert len(calls.calls) == 2
    assert "doesn't match the schema" in calls.calls[1]["messages"][-1]["content"]


def test_gives_up_after_second_mismatch(fake_openrouter):
    fake_openrouter(["not json", "still not json"])
    with pytest.raises(ExtractionError, match="didn't fit the invoice schema"):
        run(extraction.extract_invoice(b"%PDF", text="INVOICE"))


def test_scan_without_claude_asks_for_manual_entry(fake_openrouter, monkeypatch):
    fake_openrouter([])
    monkeypatch.setattr(extraction, "anthropic_configured", lambda: False)
    with pytest.raises(ExtractionError, match="scan with no text layer"):
        run(extraction.extract_invoice(b"%PDF", text=None))
