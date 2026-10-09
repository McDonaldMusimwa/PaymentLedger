"""Reads a vendor invoice PDF into structured fields with Claude."""

import base64
import os
from functools import lru_cache
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from app.config import settings

SYSTEM_PROMPT = """\
You read vendor invoices for an accounts-payable system. The fields you return are \
matched against purchase orders and goods receipts, and a match can release a payment, \
so accuracy matters more than completeness.

Copy values as printed on the document. If a field is not on the document, return null \
rather than inferring or calculating it. Dates are YYYY-MM-DD. Amounts are plain numbers \
without currency symbols or thousands separators. The PO number is the buyer's purchase \
order reference, which invoices label variously (PO, P.O. No., Order ref, Your ref).

Use `notes` for anything a reviewer should know: unreadable values, handwritten \
corrections, totals on the document that don't add up, or signs it is not a genuine \
invoice (a quote, a statement, a payment-details change request)."""


class ExtractedLine(BaseModel):
    sku: str | None = Field(description="Item code or SKU, if printed")
    description: str
    quantity: float
    unit_price: float
    amount: float = Field(description="Line total as printed")


class ExtractedInvoice(BaseModel):
    is_invoice: bool = Field(description="False for quotes, statements, receipts or non-invoices")
    vendor_name: str | None
    invoice_number: str | None
    po_number: str | None
    invoice_date: str | None = Field(description="YYYY-MM-DD")
    due_date: str | None = Field(description="YYYY-MM-DD, only if printed")
    payment_terms: str | None = Field(description="As printed, e.g. 'Net 30' or '2/10 Net 30'")
    currency: str | None = Field(description="ISO code as printed, e.g. USD, EUR, USDC")
    subtotal: float | None
    tax: float | None
    total: float | None
    lines: list[ExtractedLine]
    confidence: float = Field(description="0 to 1: how sure you are every field is right")
    notes: str


class ExtractionError(Exception):
    pass


# Env vars / profile locations the SDK resolves credentials from.
_CREDENTIAL_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
)


def credentials_configured() -> bool:
    return (
        any(os.environ.get(name) for name in _CREDENTIAL_ENV)
        or (Path.home() / ".config" / "anthropic").exists()
    )


@lru_cache
def _client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic()


async def extract_invoice(pdf: bytes) -> tuple[ExtractedInvoice, str]:
    """Returns the extracted fields and the model that produced them."""
    if not credentials_configured():
        raise ExtractionError("Extraction is not configured (set ANTHROPIC_API_KEY)")
    try:
        response = await _client().beta.messages.parse(
            model=settings.extraction_model,
            max_tokens=16000,
            output_config={"effort": "medium"},
            # On a safety decline, the API retries on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.standard_b64encode(pdf).decode(),
                            },
                        },
                        {"type": "text", "text": "Extract this invoice."},
                    ],
                }
            ],
            output_format=ExtractedInvoice,
        )
    except anthropic.AuthenticationError as exc:
        raise ExtractionError("Extraction is not configured (no valid ANTHROPIC_API_KEY)") from exc
    except anthropic.RateLimitError as exc:
        raise ExtractionError("Extraction is rate limited; retry shortly") from exc
    except anthropic.APIStatusError as exc:
        raise ExtractionError(f"Extraction failed ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ExtractionError("Could not reach the extraction service") from exc

    if response.stop_reason == "refusal":
        raise ExtractionError("The model declined to read this document")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("Invoice too long to extract in one pass")
    if response.parsed_output is None:
        raise ExtractionError("The model returned no structured result")
    return response.parsed_output, response.model
