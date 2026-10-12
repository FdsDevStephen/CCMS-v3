"""
Text extraction for Government Orders (PDF or image), English + Kannada.

PDFs whose pages all have a text layer use it (instant and exact).
Otherwise, with OCR_ENGINE=api, the whole file goes to the OCR server
(OCR_API_URL), which handles Kannada, handwriting and rotated pages far
better than Tesseract. If the server fails, or OCR_ENGINE=tesseract, each
page without a text layer is rendered and run through Tesseract with
OCR_LANG ("eng+kan"), in parallel; pytesseract shells out to the Tesseract
binary, so threads give real parallelism.

Adapted from CCMS v2's ocr_v2.py (text layer probe, grayscale rendering,
user_defined_dpi), without its writ-petition specific page selection.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import requests
import pymupdf as fitz
import numpy as np
import pytesseract

from config import settings

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | PDF_SUFFIXES

pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD


@dataclass
class OCRResult:
    text: str
    pages: list[str]
    # "text-layer", "api", "ocr" (Tesseract) or "mixed"
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

    pages = None

    if _use_api(path):
        try:
            pages = _api_pages(path)
            methods = ["api"] * len(pages)
        except Exception as e:
            print(f"[OCR] {path.name} | OCR server failed, using Tesseract: "
                  f"{e}", flush=True)

    if pages is None:
        if suffix in PDF_SUFFIXES:
            pages, methods = _pdf_pages(path)
        else:
            pages, methods = [_ocr_gray(_read_image(path))], ["ocr"]

    pages = [clean_text(page) for page in pages]
    method = methods[0] if len(set(methods)) == 1 else "mixed"

    return OCRResult(
        text="\n\n".join(page for page in pages if page),
        pages=pages,
        method=method,
        seconds=time.perf_counter() - start,
        page_methods=methods,
    )


def check_languages() -> list[str]:
    """Languages from OCR_LANG that Tesseract does not have installed."""
    installed = set(pytesseract.get_languages(config=""))
    return [
        lang for lang in settings.OCR_LANG.split("+")
        if lang not in installed
    ]


# ==========================================================
# OCR SERVER
# ==========================================================

_PAGE_MARK = re.compile(r"^=+\s*Source page (\d+)\s*=+\s*$", re.M)


def _use_api(path: Path) -> bool:
    """API unless disabled, or a PDF whose every page has a text layer."""
    if settings.OCR_ENGINE.strip().lower() != "api":
        return False

    if path.suffix.lower() not in PDF_SUFFIXES:
        return True

    with fitz.open(str(path)) as document:
        page_count = min(document.page_count, settings.OCR_MAX_PAGES)

        return not all(
            len(document[n].get_text("text").strip())
            >= settings.OCR_MIN_TEXT_LAYER_CHARS
            for n in range(page_count)
        )


def _api_pages(path: Path) -> list[str]:
    """
    POST the file as multipart "file". The server answers plain text with
    "===== Source page N =====" before each page (none for an image).
    """
    with open(path, "rb") as file:
        response = requests.post(
            settings.OCR_API_URL,
            files={"file": (path.name, file)},
            timeout=settings.OCR_API_TIMEOUT,
        )

    response.raise_for_status()
    response.encoding = "utf-8"
    text = response.text

    if not text.strip():
        raise ValueError("empty response")

    parts = _PAGE_MARK.split(text)

    if len(parts) == 1:
        return [text]

    # [before, "1", page 1, "2", page 2, ...]
    pages = parts[2::2]
    return pages[:settings.OCR_MAX_PAGES]


# ==========================================================
# PDF
# ==========================================================


def _pdf_pages(path: Path) -> tuple[list[str], list[str]]:
    with fitz.open(str(path)) as document:
        page_count = min(document.page_count, settings.OCR_MAX_PAGES)

        text_layer = {}

        for number in range(page_count):
            text = document[number].get_text("text", sort=True)

            if len(text.strip()) >= settings.OCR_MIN_TEXT_LAYER_CHARS:
                text_layer[number] = text

    to_ocr = [n for n in range(page_count) if n not in text_layer]

    ocr_text: dict[int, str] = {}

    if to_ocr:
        workers = max(1, min(settings.OCR_MAX_WORKERS, len(to_ocr)))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for number, text in zip(
                to_ocr,
                pool.map(lambda n: _ocr_pdf_page(path, n), to_ocr),
            ):
                ocr_text[number] = text

    pages, methods = [], []

    for number in range(page_count):
        if number in text_layer:
            pages.append(text_layer[number])
            methods.append("text-layer")
        else:
            pages.append(ocr_text.get(number, ""))
            methods.append("ocr")

    return pages, methods


def _ocr_pdf_page(path: Path, number: int) -> str:
    # fitz documents are not thread safe: open one per call.
    with fitz.open(str(path)) as document:
        pixmap = document[number].get_pixmap(
            dpi=settings.OCR_DPI,
            colorspace=fitz.csGRAY,
        )

    gray = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width
    )

    return _ocr_gray(gray)


# ==========================================================
# IMAGES
# ==========================================================


def _read_image(path: Path) -> np.ndarray:
    # np.fromfile + imdecode handles non-ASCII Windows paths.
    data = np.fromfile(str(path), dtype=np.uint8)
    gray = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)

    if gray is None:
        raise ValueError(f"Could not read image: {path.name}")

    # Phone photos / low-res scans: upscale so text is ~300 DPI sized.
    if gray.shape[1] < 1800:
        scale = 2500 / gray.shape[1]
        gray = cv2.resize(gray, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)

    return gray


# ==========================================================
# TESSERACT
# ==========================================================


# Minimum Tesseract OSD confidence before a page is rotated.
MIN_ORIENTATION_CONF = 2.0


def _upright(gray: np.ndarray) -> np.ndarray:
    """
    Rotate pages scanned sideways or upside down. OSD runs on a half-size
    copy (~0.5s per page); pages with too little text to judge are kept.
    """
    small = cv2.resize(gray, None, fx=0.5, fy=0.5,
                       interpolation=cv2.INTER_AREA)

    try:
        osd = pytesseract.image_to_osd(
            small,
            config="--psm 0 -c min_characters_to_try=20",
            output_type=pytesseract.Output.DICT,
        )
    except pytesseract.TesseractError:
        return gray

    if osd["orientation_conf"] < MIN_ORIENTATION_CONF:
        return gray

    # OSD "rotate" is the clockwise rotation that makes the page upright.
    turns = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }

    if osd["rotate"] in turns:
        logger.info("Rotating page by %s degrees", osd["rotate"])
        return cv2.rotate(gray, turns[osd["rotate"]])

    return gray


def _ocr_gray(gray: np.ndarray) -> str:
    """
    PSM 6 (uniform block of text) suits GO body text. user_defined_dpi
    matters because a numpy array carries no resolution metadata.
    """
    gray = _upright(gray)

    return pytesseract.image_to_string(
        gray,
        lang=settings.OCR_LANG,
        config=f"--oem 3 --psm 6 -c user_defined_dpi={settings.OCR_DPI}",
    )


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
