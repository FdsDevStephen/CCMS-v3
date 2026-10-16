"""
Text extraction for Government Orders (PDF or image), English + Kannada.

PDFs whose pages all have a text layer use it (instant and exact).
Everything else (scanned PDFs, images) goes to the OCR server
(OCR_API_URL), which handles Kannada, handwriting and rotated pages.
There is no local OCR: if the server fails, the document fails.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz
import requests

from config import settings

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | PDF_SUFFIXES

# Seconds to wait before retrying when the OCR server fails (5xx or no
# answer); the server drops requests now and then.
RETRY_WAITS = (10, 30)


@dataclass
class OCRResult:
    text: str
    pages: list[str]
    # "text-layer" or "api"
    method: str
    seconds: float
    page_methods: list[str] = field(default_factory=list)


# ==========================================================
# PUBLIC
# ==========================================================


def extract_text(path: str | Path) -> OCRResult:
    """Text of a PDF or image file, page by page."""
    path = Path(path)
    suffix = path.suffix.lower()
    start = time.perf_counter()

    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported file type '{suffix}'. "
            f"Use PDF or an image ({', '.join(sorted(IMAGE_SUFFIXES))})."
        )

    pages = _text_layer(path) if suffix in PDF_SUFFIXES else None
    method = "text-layer"

    if pages is None:
        pages = _api_pages(path)
        method = "api"

    pages = [clean_text(page) for page in pages]

    return OCRResult(
        text="\n\n".join(page for page in pages if page),
        pages=pages,
        method=method,
        seconds=time.perf_counter() - start,
        page_methods=[method] * len(pages),
    )


# ==========================================================
# PDF TEXT LAYER
# ==========================================================


def _text_layer(path: Path) -> list[str] | None:
    """The PDF's own text, if every page has one; else None (scanned)."""
    with fitz.open(str(path)) as document:
        page_count = min(document.page_count, settings.OCR_MAX_PAGES)
        pages = [document[n].get_text("text", sort=True)
                 for n in range(page_count)]

    if pages and all(len(page.strip()) >= settings.OCR_MIN_TEXT_LAYER_CHARS
                     for page in pages):
        return pages

    return None


# ==========================================================
# OCR SERVER
# ==========================================================


def _api_pages(path: Path) -> list[str]:
    """
    The file's pages from the OCR server. Pages that come back as garbage (a
    page scanned upside down or sideways) are sent again turned 180, 90
    and 270 degrees, and the best reading is kept.
    """
    pages = _post(path.name, path.read_bytes())[:settings.OCR_MAX_PAGES]

    if not any(page.strip() for page in pages):
        raise ValueError(f"OCR server returned no text for {path.name}")

    for number, page in enumerate(pages):
        if _garbled(page):
            pages[number] = _turned(path, number, page)

    return pages


def _turned(path: Path, number: int, page: str) -> str:
    """Page `number` OCR'd again turned until it reads as text."""
    best, best_score = page, _score(page)

    for angle in (180, 90, 270):
        print(f"[OCR] {path.name} | page {number + 1} unreadable, trying it "
              f"turned {angle} degrees", flush=True)
        text = _post(f"{path.stem}_p{number + 1}.pdf",
                     _page_pdf(path, number, angle))[0]

        if _score(text) > best_score:
            best, best_score = text, _score(text)

        logger.info("%s page %d turned %d degrees: score %.0f (best %.0f)",
                    path.name, number + 1, angle, _score(text), best_score)

        if not _garbled(text):
            return text

    logger.warning("%s page %d could not be read in any orientation",
                   path.name, number + 1)
    return best


def _page_pdf(path: Path, number: int, angle: int) -> bytes:
    """One page of the file (PDF or image) as a PDF, turned by `angle`."""
    with fitz.open(str(path)) as source:
        pdf = source if source.is_pdf else fitz.open(
            "pdf", source.convert_to_pdf()
        )

        with fitz.open() as page:
            page.insert_pdf(pdf, from_page=number, to_page=number)
            page[0].set_rotation((page[0].rotation + angle) % 360)
            return page.tobytes()


# Upside-down Kannada comes back from the OCR model as another script
# (Telugu) or the same few lines repeated.
_KANNADA_LATIN = re.compile(r"[ಀ-೿A-Za-z]")
_OTHER_INDIC = re.compile(r"[ऀ-౿ഀ-෿]")


def _garbled(text: str) -> bool:
    readable = len(_KANNADA_LATIN.findall(text))
    other = len(_OTHER_INDIC.findall(text))

    if other > 0.2 * (readable + other):
        return True

    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines) >= 10 and len(set(lines)) < 0.5 * len(lines)


def _score(text: str) -> float:
    """More readable letters and fewer repeated lines is better."""
    lines = [line for line in text.splitlines() if line.strip()]
    unique = len(set(lines)) / len(lines) if lines else 0
    readable = len(_KANNADA_LATIN.findall(text))
    other = len(_OTHER_INDIC.findall(text))
    return (readable - 3 * other) * unique


def _post(name: str, data: bytes) -> list[str]:
    """
    POST a file as multipart "file" to the OCR server; its pages.
    Retried on 5xx and connection errors, not on 4xx or a timeout (the
    document is too long for the server; retrying only wastes minutes).
    """
    start = time.perf_counter()

    for attempt, wait in enumerate((*RETRY_WAITS, None), start=1):
        try:
            response = requests.post(
                settings.OCR_API_URL,
                files={"file": (name, data)},
                timeout=settings.OCR_API_TIMEOUT,
            )

            if response.status_code < 500:
                break
            error = f"HTTP {response.status_code}"
        except requests.Timeout:
            raise RuntimeError(
                f"OCR server timed out after {settings.OCR_API_TIMEOUT}s "
                f"on {name}"
            )
        except requests.ConnectionError as e:
            error = str(e)

        if wait is None:
            raise RuntimeError(
                f"OCR server at {settings.OCR_API_URL} failed {attempt} "
                f"times on {name}: {error}"
            )

        print(f"[OCR] {name} | server failed ({error}), retrying in "
              f"{wait}s", flush=True)
        time.sleep(wait)

    response.raise_for_status()
    pages = _pages(response)
    logger.info("OCR request %s: %.1fs (%d attempt(s)), %d page(s), "
                "%d chars per page", name, time.perf_counter() - start,
                attempt, len(pages), sum(map(len, pages)) // max(len(pages), 1))
    return pages


# Plain-text answer: "===== Source page N =====" before each page (none
# for a single page or an image).
_PAGE_MARK = re.compile(r"^=+\s*Source page (\d+)\s*=+\s*$", re.M)


def _pages(response: requests.Response) -> list[str]:
    """
    The pages of the server's answer, in either format:
      plain text   (OCR server at 101.53.140.252/ocr) with page marks
      JSON         (IndicOCR) {"pages": [{"page_number", "markdown"}]}
    """
    if "json" in response.headers.get("content-type", ""):
        return [clean_markdown(page.get("markdown") or "")
                for page in response.json()["pages"]]

    response.encoding = "utf-8"
    parts = _PAGE_MARK.split(response.text)

    if len(parts) == 1:
        return [response.text]

    # [before, "1", page 1, "2", page 2, ...]
    return parts[2::2]


def clean_markdown(markdown: str) -> str:
    """
    IndicOCR markdown -> plain text (as CcmsApi's IndicOcrClient): "▁"
    back to spaces, one table row per line, no tags or <img> labels.
    """
    text = markdown.replace("▁", " ")
    text = re.sub(r"<img[^>]*>", "", text)
    text = re.sub(r"<br\s*/?>", " / ", text)
    text = re.sub(r"</t[hd]>\s*<t[hd][^>]*>", " | ", text)
    text = re.sub(r"</tr>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [re.sub(r"(\s*\|)+\s*", " | ",
                    re.sub(r"[ \t]+", " ", line)).strip(" |")
             for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ==========================================================
# CLEANUP
# ==========================================================

_REPLACEMENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ", "ﬁ": "fi",
    "ﬂ": "fl",
}


def clean_text(text: str) -> str:
    """Normalise quotes/dashes and spacing, keep line and paragraph breaks."""
    for old, new in _REPLACEMENTS.items():
        text = text.replace(old, new)

    # Common OCR slip in these documents: "M.S_ Building" -> "M.S. Building"
    text = re.sub(r"(?<=[A-Z])_(?=\s)", ".", text)

    # Kannada zero typed for the anusvara inside words: "ಸ೦ಖ್ಯೆ" -> "ಸಂಖ್ಯೆ".
    # Only after a Kannada letter/vowel sign, so Kannada numbers are kept.
    text = re.sub(r"(?<=[ಀ-೥])೦", "ಂ", text)

    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)

    return re.sub(r"\n{3,}", "\n\n", text).strip()
