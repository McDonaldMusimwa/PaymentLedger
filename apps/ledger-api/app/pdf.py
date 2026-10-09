"""Opens uploaded PDFs with pypdf: validates them and pulls out the text layer."""

import io
import logging
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PyPdfError

MAX_PAGES = 50
# Below this many non-whitespace characters the PDF is treated as a scan with no usable text.
MIN_TEXT_CHARS = 40

# pypdf logs a warning for every minor spec deviation, which real-world invoices are full of.
logging.getLogger("pypdf").setLevel(logging.ERROR)


class InvalidPdfError(ValueError):
    pass


@dataclass
class PdfContent:
    page_count: int
    text: str  # pages separated by form feeds; empty for scanned PDFs

    @property
    def has_text(self) -> bool:
        return has_usable_text(self.text)


def has_usable_text(text: str | None) -> bool:
    return bool(text) and sum(not c.isspace() for c in text) >= MIN_TEXT_CHARS


def read_pdf(data: bytes) -> PdfContent:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise InvalidPdfError("PDF is password-protected")
        page_count = len(reader.pages)
        if page_count == 0:
            raise InvalidPdfError("PDF has no pages")
        if page_count > MAX_PAGES:
            raise InvalidPdfError(f"PDF has {page_count} pages; the limit is {MAX_PAGES}")
        # Layout mode keeps columns aligned, so line items stay readable as rows.
        pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    except InvalidPdfError:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError) as exc:
        raise InvalidPdfError(f"Not a readable PDF: {exc}") from exc
    return PdfContent(page_count=page_count, text="\f".join(pages).strip())
