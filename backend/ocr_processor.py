"""
FinSight - OCR Processor
Uses Tesseract OCR to extract text from PDFs and images, then applies
regex patterns to pull out financial fields (amounts, dates, vendors).
"""

import os
import re
import logging
import tempfile
from pathlib import Path
from typing import Any

import pytesseract
from PIL import Image
from pdf2image import convert_from_path

logger = logging.getLogger(__name__)

# ── Regex patterns for financial data extraction ──────────────────────────────
# Matches dollar amounts like $1,234.56 | $0.99 | $ 12,000
AMOUNT_PATTERN = re.compile(
    r"\$\s*[\d,]+(?:\.\d{1,2})?|\b[\d,]+\.\d{2}\s*(?:USD|usd)?\b"
)

# Matches common date formats: MM/DD/YYYY, YYYY-MM-DD, Month DD YYYY, DD-Mon-YYYY
DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
    r"|\d{4}[/\-]\d{1,2}[/\-]\d{1,2}"
    r"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)

# Matches invoice / receipt numbers
INVOICE_PATTERN = re.compile(
    r"(?:invoice|receipt|order|ref(?:erence)?|txn|transaction)[^\w]?\s*[#:]?\s*([\w\-]+)",
    re.IGNORECASE,
)

# Matches tax identifiers (EIN, SSN, TIN) — redacted in output for privacy
TAX_ID_PATTERN = re.compile(
    r"\b(?:EIN|TIN|SSN)[:\s]*(\d{2}[- ]\d{7}|\d{3}[- ]\d{2}[- ]\d{4})\b",
    re.IGNORECASE,
)

# Common vendor signal words to help extract vendor name lines
VENDOR_SIGNALS = [
    "from:", "vendor:", "billed by:", "company:", "merchant:", "payee:", "paid to:",
]


class OCRProcessor:
    """Wraps Tesseract OCR and financial field extraction logic."""

    def __init__(self):
        # On EC2 (Ubuntu) Tesseract is at /usr/bin/tesseract.
        # Override via TESSERACT_CMD env var if needed.
        tesseract_cmd = os.getenv("TESSERACT_CMD", "/usr/bin/tesseract")
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        logger.info("Tesseract binary: %s", tesseract_cmd)

    # ── Public API ────────────────────────────────────────────────────────────
    def process_file(self, file_path: str) -> dict[str, Any]:
        """
        Main entry point.
        Accepts a local file path (PDF or image).
        Returns a structured dict with raw text and extracted financial fields.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        extension = path.suffix.lower()
        logger.info("Processing file: %s (type: %s)", path.name, extension)

        if extension == ".pdf":
            raw_text = self._ocr_pdf(str(path))
        else:
            raw_text = self._ocr_image(str(path))

        fields = self._extract_fields(raw_text)
        confidence = self._estimate_confidence(raw_text)

        result = {
            "raw_text": raw_text,
            "fields": fields,
            "confidence": confidence,
            "page_count": fields.pop("_page_count", 1),
        }
        logger.info(
            "Extraction complete — %d chars, confidence %.2f, fields: %s",
            len(raw_text),
            confidence,
            list(fields.keys()),
        )
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────
    def _ocr_pdf(self, pdf_path: str) -> str:
        """Convert each PDF page to an image, then run Tesseract on each."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                pages = convert_from_path(
                    pdf_path,
                    dpi=300,           # 300 DPI gives good OCR accuracy
                    output_folder=tmp_dir,
                    fmt="png",
                    thread_count=2,
                )
            except Exception as exc:
                logger.error("pdf2image conversion failed: %s", exc)
                raise RuntimeError(f"PDF conversion failed: {exc}") from exc

            texts = []
            for i, page_img in enumerate(pages):
                try:
                    text = pytesseract.image_to_string(
                        page_img,
                        config="--psm 6 --oem 3",  # assume uniform text block
                        lang="eng",
                    )
                    texts.append(text)
                    logger.debug("OCR page %d: %d characters", i + 1, len(text))
                except Exception as exc:
                    logger.warning("OCR failed on page %d: %s", i + 1, exc)
                    texts.append("")

        full_text = "\n\n--- PAGE BREAK ---\n\n".join(texts)
        # Store page count as a side-channel field
        self._last_page_count = len(pages)
        return full_text

    def _ocr_image(self, image_path: str) -> str:
        """Run Tesseract on a single image file."""
        try:
            img = Image.open(image_path)
            # Convert to RGB if needed (e.g., RGBA PNGs)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            text = pytesseract.image_to_string(
                img,
                config="--psm 6 --oem 3",
                lang="eng",
            )
            self._last_page_count = 1
            return text
        except Exception as exc:
            logger.error("Image OCR failed: %s", exc)
            raise RuntimeError(f"Image OCR failed: {exc}") from exc

    def _extract_fields(self, text: str) -> dict[str, Any]:
        """
        Apply regex patterns to the raw OCR text.
        Returns a dict of field_name → value(s).
        """
        fields: dict[str, Any] = {}
        fields["_page_count"] = getattr(self, "_last_page_count", 1)

        # ── Amounts ──────────────────────────────────────────────────────────
        raw_amounts = AMOUNT_PATTERN.findall(text)
        parsed_amounts = []
        for amt_str in raw_amounts:
            cleaned = re.sub(r"[^\d.]", "", amt_str)
            try:
                parsed_amounts.append(float(cleaned))
            except ValueError:
                pass

        if parsed_amounts:
            fields["amounts"] = parsed_amounts
            fields["total_amount"] = self._find_total_amount(text, parsed_amounts)
            fields["min_amount"] = min(parsed_amounts)
            fields["amount_count"] = len(parsed_amounts)

        # ── Dates ─────────────────────────────────────────────────────────────
        dates = DATE_PATTERN.findall(text)
        if dates:
            fields["dates"] = list(dict.fromkeys(dates))  # deduplicate, preserve order
            fields["primary_date"] = dates[0]

        # ── Invoice / reference number ────────────────────────────────────────
        inv_match = INVOICE_PATTERN.search(text)
        if inv_match:
            fields["invoice_number"] = inv_match.group(1).strip()

        # ── Vendor name ───────────────────────────────────────────────────────
        vendor = self._extract_vendor(text)
        if vendor:
            fields["vendor_name"] = vendor

        # ── Tax ID (masked for privacy) ───────────────────────────────────────
        tax_match = TAX_ID_PATTERN.search(text)
        if tax_match:
            fields["tax_id"] = "***REDACTED***"

        # ── Document type heuristic ───────────────────────────────────────────
        fields["document_type"] = self._detect_document_type(text)

        return fields

    def _find_total_amount(self, text: str, parsed_amounts: list) -> float:
        """
        Find the most likely total amount in the document.
        Scans for explicit label patterns first (Total, Amount Due, Net Pay, etc.)
        then falls back to the largest amount found.
        """
        # Patterns ordered by specificity / reliability
        total_patterns = [
            r"(?:grand\s+)?total\s+amount\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
            r"amount\s+(?:due|owed|payable)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
            r"total\s+(?:due|payable|charges?|cost)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
            r"(?:^|\n)\s*total\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)(?:\s|$)",
            r"net\s+(?:pay|salary|wages|income|amount)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
            r"gross\s+(?:pay|salary|wages|income)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
            r"balance\s+(?:due|forward|payable)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
            r"(?:you\s+(?:owe|paid|save[d]?))\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
            r"(?:sub\s*)?total\s*[:\-]\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
        ]
        for pattern in total_patterns:
            m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if m:
                try:
                    val = float(re.sub(r"[^\d.]", "", m.group(1)))
                    if val > 0:
                        return val
                except ValueError:
                    pass

        # Fall back to the largest amount found
        return max(parsed_amounts) if parsed_amounts else 0.0

    def _extract_vendor(self, text: str) -> str | None:
        """
        Try to extract a vendor / merchant name using known signal words.
        Falls back to looking at the first non-empty line of the document.
        """
        text_lower = text.lower()
        for signal in VENDOR_SIGNALS:
            idx = text_lower.find(signal)
            if idx != -1:
                # Take the rest of the line after the signal keyword
                after = text[idx + len(signal):].split("\n")[0].strip()
                if after:
                    return after[:100]  # cap at 100 chars

        # Fallback: first non-empty line (usually the letterhead)
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and len(stripped) > 3:
                return stripped[:100]

        return None

    def _detect_document_type(self, text: str) -> str:
        """
        Classify the document type based on keyword presence in the OCR text.
        """
        text_lower = text.lower()
        type_keywords = {
            "bank_statement": ["account statement", "bank statement", "opening balance",
                               "closing balance", "transaction history"],
            "invoice":        ["invoice", "bill to", "due date", "payment due", "tax invoice"],
            "receipt":        ["receipt", "thank you for your purchase", "amount paid",
                               "payment received", "pos receipt"],
            "tax_form":       ["form w-2", "form 1099", "schedule c", "irs", "tax return",
                               "taxable income"],
            "pay_stub":       ["pay stub", "payroll", "gross pay", "net pay", "ytd"],
            "insurance":      ["policy number", "insurance", "premium", "deductible", "claim"],
        }
        scores: dict[str, int] = {}
        for doc_type, keywords in type_keywords.items():
            scores[doc_type] = sum(1 for kw in keywords if kw in text_lower)

        best = max(scores, key=lambda k: scores[k])
        return best if scores[best] > 0 else "unknown"

    def _estimate_confidence(self, text: str) -> float:
        """
        Rough confidence estimate: ratio of alphanumeric chars to total chars.
        A garbled OCR result will have many symbol/garbage characters.
        Returns a value between 0.0 and 1.0.
        """
        if not text:
            return 0.0
        alnum_count = sum(1 for c in text if c.isalnum() or c.isspace())
        score = alnum_count / len(text)
        return round(min(score, 1.0), 4)
