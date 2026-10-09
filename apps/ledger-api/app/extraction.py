"""Turns a vendor invoice into structured fields with an LLM.

pypdf pulls the text layer out of the PDF first. That text goes to the configured provider:

- openrouter: a free OpenRouter model reads the text. Free models can't read images, so
  scanned PDFs (no text layer) fall through to Claude when it's configured.
- anthropic: Claude reads the text, or the PDF itself when it's a scan.

The output is validated against `ExtractedInvoice` either way; the match engine, not the
model, decides whether the invoice gets paid.
"""

import base64
import os
import re
from functools import lru_cache
from pathlib import Path

import anthropic
import openai
from pydantic import BaseModel, Field, ValidationError

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

TEXT_INSTRUCTION = (
    "Extract this invoice. The text was pulled from the PDF's text layer with its layout "
    "approximated by spacing, so columns may be slightly misaligned."
)


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


def _invoice_text(text: str) -> str:
    return f"<invoice_text>\n{text}\n</invoice_text>\n\n{TEXT_INSTRUCTION}"


async def extract_invoice(pdf: bytes, text: str | None = None) -> tuple[ExtractedInvoice, str]:
    """Returns the extracted fields and the model that produced them.

    Pass `text` (from pypdf) when the PDF has a usable text layer; None means it's a scan.
    """
    if settings.extraction_provider == "openrouter":
        if text:
            return await _extract_openrouter(text)
        if not anthropic_configured():
            raise ExtractionError(
                "This PDF is a scan with no text layer, and the free model can only read text. "
                "Enter the fields by hand, or set ANTHROPIC_API_KEY so scans can be read."
            )
    return await _extract_anthropic(pdf, text)


# ---- OpenRouter (OpenAI-compatible API) ----


def _strict_schema(schema: dict) -> dict:
    """Pydantic's JSON schema, tightened for strict structured output: every property
    required and no extra keys, recursively."""
    if isinstance(schema, dict):
        schema = {k: _strict_schema(v) for k, v in schema.items()}
        if schema.get("type") == "object" and "properties" in schema:
            schema["required"] = list(schema["properties"])
            schema["additionalProperties"] = False
        return schema
    if isinstance(schema, list):
        return [_strict_schema(v) for v in schema]
    return schema


INVOICE_SCHEMA = _strict_schema(ExtractedInvoice.model_json_schema())


@lru_cache
def _openrouter() -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        default_headers={"X-Title": "PaymentLedger"},
        timeout=120,
    )


def _parse_json(content: str) -> ExtractedInvoice:
    # Some models wrap JSON in a markdown fence despite the response format.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    return ExtractedInvoice.model_validate_json(fenced.group(1) if fenced else content)


async def _extract_openrouter(text: str) -> tuple[ExtractedInvoice, str]:
    models = settings.openrouter_model_list
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _invoice_text(text)},
    ]
    # Free models don't all honor the schema perfectly: validate, and give one chance to fix.
    for attempt in range(2):
        try:
            response = await _openrouter().chat.completions.create(
                model=models[0],
                messages=messages,
                temperature=0,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "invoice", "strict": True, "schema": INVOICE_SCHEMA},
                },
                # OpenRouter tries these in order if the first is down or rate limited.
                extra_body={"models": models} if len(models) > 1 else None,
            )
        except openai.AuthenticationError as exc:
            raise ExtractionError("OpenRouter rejected the API key (OPENROUTER_API_KEY)") from exc
        except openai.RateLimitError as exc:
            raise ExtractionError(
                "Free-model rate limit reached on OpenRouter; retry later or add credits"
            ) from exc
        except openai.APIStatusError as exc:
            raise ExtractionError(f"OpenRouter error ({exc.status_code}): {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise ExtractionError("Could not reach OpenRouter") from exc

        if not response.choices or not response.choices[0].message.content:
            raise ExtractionError("The model returned an empty response")
        content = response.choices[0].message.content
        if response.choices[0].finish_reason == "length":
            raise ExtractionError("Invoice too long for the model to extract in one pass")
        try:
            return _parse_json(content), f"openrouter:{response.model}"
        except ValidationError as exc:
            if attempt == 1:
                raise ExtractionError(
                    f"The model's output didn't fit the invoice schema: {exc}"
                ) from exc
            messages += [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": "That JSON doesn't match the schema. Errors:\n"
                    f"{exc}\n\nReturn only the corrected JSON object.",
                },
            ]
    raise AssertionError("unreachable")


# ---- Anthropic (Claude) ----

# Env vars / profile locations the SDK resolves credentials from.
_CREDENTIAL_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
)


def anthropic_configured() -> bool:
    return (
        any(os.environ.get(name) for name in _CREDENTIAL_ENV)
        or (Path.home() / ".config" / "anthropic").exists()
    )


@lru_cache
def _anthropic() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic()


def _anthropic_content(pdf: bytes, text: str | None) -> list[dict]:
    if text:
        return [{"type": "text", "text": _invoice_text(text)}]
    return [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf).decode(),
            },
        },
        {"type": "text", "text": "Extract this invoice."},
    ]


async def _extract_anthropic(pdf: bytes, text: str | None) -> tuple[ExtractedInvoice, str]:
    if not anthropic_configured():
        raise ExtractionError(
            "Extraction is not configured: set OPENROUTER_API_KEY (free) or ANTHROPIC_API_KEY"
        )
    try:
        response = await _anthropic().beta.messages.parse(
            model=settings.anthropic_model,
            max_tokens=16000,
            output_config={"effort": "medium"},
            # On a safety decline, the API retries on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _anthropic_content(pdf, text)}],
            output_format=ExtractedInvoice,
        )
    except anthropic.AuthenticationError as exc:
        raise ExtractionError("Anthropic rejected the API key (ANTHROPIC_API_KEY)") from exc
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
