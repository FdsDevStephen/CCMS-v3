"""
CCMS-v3 pipeline in one file: PDF / image -> OCR server -> document type ->
fields (rules and/or the LLM; every value is checked against the text) ->
the response as JSON.

Standalone: needs only Python 3.9 or newer. No pip packages and no other
file of this project; it talks only to the two servers below.

    python ccms_pipeline.py order.pdf                  # one file
    python ccms_pipeline.py "D:\\Orders\\March"          # every PDF / image in a folder (and subfolders)
    python ccms_pipeline.py                            # choose the folder in a folder picker
    python ccms_pipeline.py folder --out results.json  # also save the response to a file
    python ccms_pipeline.py folder --workers 4         # 4 documents at a time
    python ccms_pipeline.py order.pdf --text           # include the OCR text in the response

The response goes to the screen (stdout) as JSON: one object for one file,
a list for several. Progress goes to stderr, so

    python ccms_pipeline.py folder > results.json

saves only the JSON. Before any document is processed, both servers are
checked; if one is down the script says which and stops (exit code 2).
Exit code 1 means some documents failed (their "error" says why).

What it does, per document (the same as go_extractor/pipeline.py):
  - the OCR server reads every page (Kannada / English);
  - suspension orders (file named Invoke / Extend / Revoke, or the text says
    ಅಮಾನತು / suspension): the LLM reads the whole order for the HRMS2 data
    elements of its type; other suspension orders get the suspension fields;
  - Government Advocate GOs: rules first, then the LLM for missing fields.
Every value the LLM copies must be found in the OCR text, and every date
must be a date in the text; anything else is left empty (null).

Differences from the go_extractor package (it uses pymupdf, which is not
part of Python): a digital PDF's own text layer is not used (the OCR server
reads it like a scan), and a page the server cannot read (scanned upside
down) is not sent again turned: it is listed in "warnings" instead.

Change the servers here or with the environment variables
CCMS_OCR_URL / CCMS_LLM_URL.
"""

from __future__ import annotations

import argparse
import html
import http.client
import json
import logging
import mimetypes
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("ccms_pipeline")


# ==========================================================================
# SETTINGS
# ==========================================================================

# Scanned PDFs and images are read by this OCR server. It answers plain
# text with "===== Source page N =====" marks (IndicOCR's JSON is read too).
OCR_URL = os.environ.get("CCMS_OCR_URL", "http://101.53.140.252/ocr")
# The server takes ~2-10 s per page; long or busy documents take longer.
OCR_TIMEOUT = 900
# GOs are 1-2 pages; this only guards against huge uploads.
OCR_MAX_PAGES = 10

# The LLM: {"prompt", ...} -> {"text"} (greedy: the same prompt always
# gets the same answer).
LLM_URL = os.environ.get("CCMS_LLM_URL", "http://101.53.137.13:8004/vllm/generate_greedy")
# Suspension Invoke Orders return ~58 values, which takes a few minutes.
LLM_TIMEOUT = 300
LLM_RETRIES = 3
# Suspension orders return each field in Kannada and English (~800 tokens
# for the 11 suspension fields, more for the 33 invoke-order data elements).
LLM_MAX_TOKENS = 4096
# Must stay 1.0: higher values stop the model copying names from the text.
LLM_REPETITION_PENALTY = 1.0
# Qwen3 only: append "/no_think" to skip slow reasoning.
LLM_NO_THINK = False

# The server check before a run: an answer (e.g. 405 to a GET) within this
# many seconds means the server is up; a 5xx answer or none means down.
CHECK_TIMEOUT = 15

# ==========================================================================
# HTTP (urllib): the OCR and LLM servers
# ==========================================================================

class ServerError(Exception):
    """The server could not be reached, or dropped the connection."""


class ServerTimeout(ServerError):
    """The server did not answer in time."""


def http_request(url: str, data: bytes | None = None, headers: dict | None = None,
                 timeout: float = 60) -> tuple[int, str, bytes]:
    """
    One request: (status, content type, body). Any HTTP status is
    returned, not raised; no answer raises ServerTimeout, a refused or
    dropped connection ServerError.
    """
    request = urllib.request.Request(url, data=data, headers=headers or {},
                                     method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (response.status, response.headers.get("Content-Type", ""),
                    response.read())
    except urllib.error.HTTPError as e:
        content_type = e.headers.get("Content-Type", "") if e.headers else ""
        return e.code, content_type, e.read()
    except (socket.timeout, TimeoutError) as e:
        raise ServerTimeout(f"no answer within {timeout:.0f}s") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise ServerTimeout(f"no answer within {timeout:.0f}s") from e
        raise ServerError(str(e.reason)) from e
    except (http.client.HTTPException, OSError) as e:
        raise ServerError(str(e) or type(e).__name__) from e


def multipart(field_name: str, file_name: str, data: bytes) -> tuple[bytes, str]:
    """A multipart/form-data body with one file: (body, Content-Type)."""
    boundary = uuid.uuid4().hex
    safe_name = file_name.replace('"', "%22").replace("\r", "").replace("\n", "")
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    head = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n").encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return head + data + tail, f"multipart/form-data; boundary={boundary}"


def check_server(name: str, url: str) -> tuple[bool, str]:
    """(up, what was seen). These endpoints answer a GET with 405 when up."""
    start = time.perf_counter()
    try:
        status, _, body = http_request(url, timeout=CHECK_TIMEOUT)
    except ServerError as e:
        return False, f"{name} DOWN: {e} ({url})"
    seconds = time.perf_counter() - start
    if status >= 500:
        detail = body.decode("utf-8", "replace").strip()[:150]
        return False, f"{name} DOWN: HTTP {status} {detail} ({url})"
    return True, f"{name} up (HTTP {status} in {seconds:.1f}s, {url})"


# ==========================================================================
# DATA MODELS (was go_extractor/models.py)
# ==========================================================================

@dataclass
class LitigationOfficer:
    officer: str | None = None
    phone: str | None = None
    email: str | None = None
    # True when the same officer is also the Swearing Officer.
    is_swearing_officer: bool = False
    # Set only when a different person is named as Swearing Officer.
    swearing_officer: str | None = None


@dataclass
class IssuingAuthority:
    name: str | None = None
    designation: str | None = None


@dataclass
class GOExtraction:
    """The 8 fields extracted from a Government Order."""

    file_no: str | None = None
    go_number: str | None = None
    go_date: str | None = None
    case_number: str | None = None
    petitioner: str | None = None
    # Respondents as named in the case title ("The State of Karnataka &
    # others") plus the numbered respondents the Advocate appears for.
    respondent: str | None = None
    respondents_represented: list[str] = field(default_factory=list)
    government_advocate: str | None = None
    litigation_officer: LitigationOfficer = field(
        default_factory=LitigationOfficer
    )
    issuing_authority: IssuingAuthority = field(
        default_factory=IssuingAuthority
    )

    # Where each field came from ("rules" or "llm"), for review/debugging.
    sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def missing_fields(self) -> list[str]:
        """Top-level fields that are still empty."""
        checks = {
            "file_no": self.file_no,
            "go_number": self.go_number,
            "go_date": self.go_date,
            "case_number": self.case_number,
            "petitioner": self.petitioner,
            "respondent": self.respondent,
            "government_advocate": self.government_advocate,
            "litigation_officer": self.litigation_officer.officer,
            "issuing_authority": self.issuing_authority.name,
        }

        return [name for name, value in checks.items() if not value]


@dataclass
class SuspensionOrder:
    """Fields of an order that suspends, extends or revokes a suspension."""

    # "Suspension", "Suspension extended", "Suspension revoked" or
    # "Other (suspension related)", decided from the content.
    order_type: str | None = None
    order_number: str | None = None
    order_date: str | None = None
    employee_name: str | None = None
    employee_designation: str | None = None
    employee_office: str | None = None
    # Short English summary of why the order was passed.
    reason: str | None = None
    rule_invoked: str | None = None
    # Criminal / Lokayukta case the order refers to, if any.
    case_reference: str | None = None
    effective_from: str | None = None
    issuing_authority: IssuingAuthority = field(
        default_factory=IssuingAuthority
    )

    # Values above are in English; these are the same fields as written
    # in the order (usually Kannada), for checking against the scan.
    original: dict[str, str] = field(default_factory=dict)

    sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def missing_fields(self) -> list[str]:
        """Fields every suspension order should have that are still empty."""
        checks = {
            "order_type": self.order_type,
            "order_number": self.order_number,
            "order_date": self.order_date,
            "employee_name": self.employee_name,
            "employee_designation": self.employee_designation,
            "issuing_authority": (
                self.issuing_authority.name
                or self.issuing_authority.designation
            ),
        }

        return [name for name, value in checks.items() if not value]


@dataclass
class EmployeeElements:
    """The data elements of one employee named in an order."""

    # Element key -> value in English; None when the order does not state it.
    values: dict[str, str | None] = field(default_factory=dict)
    # Same elements as written in the order (usually Kannada).
    original: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {**self.values, "original": self.original}


@dataclass
class DisciplinaryOrder:
    """HRMS2 data elements of an order (go_extractor/data_elements.py)."""

    # Data-element order type, e.g. "Suspension Invoke Order".
    order_type: str
    # Elements of the whole order: key -> value in English, or None.
    values: dict[str, str | None] = field(default_factory=dict)
    # Same elements as written in the order (usually Kannada).
    original: dict[str, str] = field(default_factory=dict)
    # One entry per employee the order is about, in the order named.
    employees: list[EmployeeElements] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)
    # Set when the order's subject line reads as another type than the
    # one from the file name (the file name's type is kept).
    type_check: str | None = None

    def to_dict(self) -> dict:
        return {
            "order_type": self.order_type,
            "type_check": self.type_check,
            **self.values,
            "employees": [e.to_dict() for e in self.employees],
            "original": self.original,
            "sources": self.sources,
        }

    def missing_fields(self) -> list[str]:
        """Elements every order should have that are still empty."""
        missing = [name for name in ("order_no", "order_date")
                   if not self.values.get(name)]

        for name in ("employee_name", "designation"):
            if not self.employees or any(
                not e.values.get(name) for e in self.employees
            ):
                missing.append(name)

        return missing


@dataclass
class DocumentResult:
    """Everything produced for one uploaded file."""

    filename: str
    fields: GOExtraction | SuspensionOrder | DisciplinaryOrder
    text: str
    ocr_method: str
    timings: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    # "advocate" (GO authorising a Government Advocate), "suspension", or
    # a data-element order type such as "Suspension Invoke Order".
    doc_type: str = "advocate"
    pages: int = 0
    # Pages the OCR server could not read (e.g. scanned upside down).
    warnings: list[str] = field(default_factory=list)


# ==========================================================================
# OCR (was go_extractor/ocr.py): the OCR server reads every page
# ==========================================================================

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


PDF_SUFFIXES = {".pdf"}


SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | PDF_SUFFIXES


# Seconds to wait before retrying when the OCR server fails (5xx or no
# answer); the server drops requests now and then.
RETRY_WAITS = (10, 30)


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


# Plain-text answer: "===== Source page N =====" before each page (none
# for a single page or an image).
_PAGE_MARK = re.compile(r"^=+\s*Source page (\d+)\s*=+\s*$", re.M)


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


@dataclass
class OCRResult:
    text: str
    pages: list[str]
    # Always "api" here (the go_extractor package also has "text-layer").
    method: str
    seconds: float
    page_methods: list[str] = field(default_factory=list)
    # Pages the server could not read (e.g. scanned upside down).
    warnings: list[str] = field(default_factory=list)


def extract_text(path: str | Path) -> OCRResult:
    """Text of a PDF or image file, page by page, from the OCR server."""
    path = Path(path)
    suffix = path.suffix.lower()
    start = time.perf_counter()

    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported file type '{suffix}'. "
            f"Use PDF or an image ({', '.join(sorted(IMAGE_SUFFIXES))})."
        )

    pages = _post(path.name, path.read_bytes())[:OCR_MAX_PAGES]

    if not any(page.strip() for page in pages):
        raise ValueError(f"OCR server returned no text for {path.name}")

    # The go_extractor package sends such a page again turned 180 / 90 /
    # 270 degrees (with pymupdf); here it is only reported.
    warnings = [
        f"Page {number} reads as garbage (scanned upside down or sideways?)"
        for number, page in enumerate(pages, start=1) if _garbled(page)
    ]
    for warning in warnings:
        logger.warning("%s: %s", path.name, warning)

    pages = [clean_text(page) for page in pages]

    return OCRResult(
        text="\n\n".join(page for page in pages if page),
        pages=pages,
        method="api",
        seconds=time.perf_counter() - start,
        page_methods=["api"] * len(pages),
        warnings=warnings,
    )


def _post(name: str, data: bytes) -> list[str]:
    """
    POST a file as multipart "file" to the OCR server; its pages.
    Retried on 5xx and connection errors, not on 4xx or a timeout (the
    document is too long for the server; retrying only wastes minutes).
    """
    start = time.perf_counter()
    body, content_type = multipart("file", name, data)

    for attempt, wait in enumerate((*RETRY_WAITS, None), start=1):
        try:
            status, answer_type, answer = http_request(
                OCR_URL, body, {"Content-Type": content_type}, OCR_TIMEOUT
            )

            if status < 500:
                break
            error = f"HTTP {status}"
        except ServerTimeout:
            raise RuntimeError(
                f"OCR server timed out after {OCR_TIMEOUT}s on {name}"
            )
        except ServerError as e:
            error = str(e)

        if wait is None:
            raise RuntimeError(
                f"OCR server at {OCR_URL} failed {attempt} times on {name}: {error}"
            )

        print(f"[OCR] {name} | server failed ({error}), retrying in "
              f"{wait}s", file=sys.stderr, flush=True)
        time.sleep(wait)

    if status >= 400:
        raise RuntimeError(
            f"OCR server answered HTTP {status} for {name}: "
            f"{answer.decode('utf-8', 'replace')[:200]}"
        )

    pages = _pages(answer_type, answer)
    logger.info("OCR request %s: %.1fs (%d attempt(s)), %d page(s), "
                "%d chars per page", name, time.perf_counter() - start,
                attempt, len(pages), sum(map(len, pages)) // max(len(pages), 1))
    return pages


def _pages(content_type: str, body: bytes) -> list[str]:
    """
    The pages of the server's answer, in either format:
      plain text   (OCR server at 101.53.140.252/ocr) with page marks
      JSON         (IndicOCR) {"pages": [{"page_number", "markdown"}]}
    """
    if "json" in content_type:
        return [clean_markdown(page.get("markdown") or "")
                for page in json.loads(body.decode("utf-8"))["pages"]]

    text = body.decode("utf-8", "replace")
    parts = _PAGE_MARK.split(text)

    if len(parts) == 1:
        return [text]

    # [before, "1", page 1, "2", page 2, ...]
    return parts[2::2]


# ==========================================================================
# LLM CLIENT (was go_extractor/llm/clients.py): the generate_greedy server
# ==========================================================================

SYSTEM_PROMPT = (
    "You are a JSON extraction engine. Return ONLY valid JSON. "
    "Never explain. Never use markdown. Output exactly one JSON object."
)


def clean_reply(text: str) -> str:
    """Drop <think> blocks and ```json fences around the answer."""
    text = re.sub(r"<think>.*?(?:</think>|$)", "", text or "", flags=re.DOTALL)
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


class LLMClient:
    """The generate_greedy server: {"prompt", ...} -> {"text"}. One per thread."""

    name = "generate"

    def __init__(self) -> None:
        # Totals since the last reset (the pipeline resets per document).
        self.calls = 0
        self.seconds = 0.0
        # What the server said about the last call (tokens, timing).
        self.last_info: dict = {}

    def generate(self, prompt: str) -> str:
        last_error = None

        for attempt in range(1, LLM_RETRIES + 1):
            start = time.perf_counter()

            try:
                self.last_info = {}
                reply = clean_reply(self._call(prompt))
                seconds = time.perf_counter() - start
                self.calls += 1
                self.seconds += seconds
                logger.info(
                    "LLM call %d: %.1fs, prompt %d chars, reply %d chars%s",
                    self.calls, seconds, len(prompt), len(reply),
                    "".join(f", {k} {v}" for k, v in self.last_info.items()),
                )
                return reply
            except Exception as e:
                self.seconds += time.perf_counter() - start
                last_error = e
                print(
                    f"[{self.name}] Attempt {attempt}/{LLM_RETRIES} failed: {e}",
                    file=sys.stderr, flush=True,
                )
                if attempt < LLM_RETRIES:
                    time.sleep(2)

        raise RuntimeError(
            f"{self.name}: no response after {LLM_RETRIES} attempts"
        ) from last_error

    def _call(self, prompt: str) -> str:
        prompt = SYSTEM_PROMPT + "\n\n" + prompt

        if LLM_NO_THINK:
            prompt += "\n\n/no_think"

        # json.dumps escapes non-ASCII, exactly as requests' json= did.
        payload = json.dumps({
            "prompt": prompt,
            "max_tokens": LLM_MAX_TOKENS,
            "temperature": 0,
            "repetition_penalty": LLM_REPETITION_PENALTY,
        }).encode("utf-8")
        status, _, body = http_request(
            LLM_URL, payload, {"Content-Type": "application/json"}, LLM_TIMEOUT
        )

        if status >= 400:
            raise RuntimeError(
                f"HTTP {status}: {body.decode('utf-8', 'replace')[:200]}"
            )

        data = json.loads(body.decode("utf-8"))
        self.last_info = {
            key: data[key] for key in
            ("tokens_generated", "inference_ms", "finish_reason", "model")
            if key in data
        }
        return data["text"]


# ==========================================================================
# FUZZY MATCH (replaces rapidfuzz.fuzz.partial_ratio)
# ==========================================================================

def _lcs(a: str, b: str) -> int:
    """Length of the longest common subsequence (bit-parallel, Hyyrö 2004)."""
    if not a or not b:
        return 0

    masks: dict[str, int] = {}
    for i, char in enumerate(a):
        masks[char] = masks.get(char, 0) | (1 << i)

    full = (1 << len(a)) - 1
    v = full

    for char in b:
        u = v & masks.get(char, 0)
        v = ((v + u) | (v - u)) & full

    return len(a) - bin(v).count("1")


def _ratio(a: str, b: str) -> float:
    """rapidfuzz's fuzz.ratio: 100 * normalised Indel similarity."""
    total = len(a) + len(b)
    return 100.0 if not total else 200.0 * _lcs(a, b) / total


def partial_ratio(s1: str, s2: str, score_cutoff: float = 0) -> float:
    """
    rapidfuzz's fuzz.partial_ratio: the best fuzz.ratio of the shorter
    string against any part of the longer one of its length (also the
    shorter parts at either end). Below score_cutoff it returns 0.

    Parts that cannot reach score_cutoff are skipped: a part has at most
    as many characters in common with s1 as their letter counts allow.
    """
    if len(s1) > len(s2):
        s1, s2 = s2, s1

    n1, n2 = len(s1), len(s2)

    if not n1:
        return 100.0 if not n2 else 0.0

    best = 0.0
    need = max(score_cutoff, 0)
    counts: dict[str, int] = {}
    for char in s1:
        counts[char] = counts.get(char, 0) + 1

    def consider(part: str, common_bound: int) -> None:
        nonlocal best
        total = n1 + len(part)
        if 200.0 * common_bound / total < max(need, best) or not total:
            return
        score = 200.0 * _lcs(s1, part) / total
        if score > best:
            best = score

    # Shorter parts at the start and the end of s2.
    for size in range(1, n1):
        consider(s2[:size], size)
        consider(s2[n2 - size:], size)

    # Every part of s2 as long as s1; `common` = letters shared by count.
    window: dict[str, int] = {}
    common = 0
    for i, char in enumerate(s2):
        if window.get(char, 0) < counts.get(char, 0):
            common += 1
        window[char] = window.get(char, 0) + 1

        if i >= n1:
            gone = s2[i - n1]
            if window[gone] <= counts.get(gone, 0):
                common -= 1
            window[gone] -= 1

        if i >= n1 - 1:
            consider(s2[i - n1 + 1:i + 1], common)
            if best == 100.0:
                break

    return best if best >= need else 0.0


# ==========================================================================
# GOVERNMENT ADVOCATE GOs: RULES (was go_extractor/rules.py)
# Rule-based field extraction for Karnataka Government Orders that authorise
# a Government Advocate to appear in a court case.
#
# These GOs follow a fixed template, so each field is anchored on its
# standard wording ("GOVERNMENT ORDER NO", "filed by ... V/S ...",
# "is authorized to appear", "shall be the Litigation Conducting Officer",
# "BY ORDER AND IN THE NAME OF THE GOVERNOR ..."). Kannada labels are
# matched where the value itself is unambiguous (numbers, dates). Anything
# the rules cannot find is left None for the LLM fallback.
# ==========================================================================

# ==========================================================
# HELPERS
# ==========================================================

_KANNADA_DIGITS = str.maketrans("೦೧೨೩೪೫೬೭೮೯", "0123456789")

I = re.IGNORECASE
S = re.DOTALL

DATE = (
    r"\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}"
)


def prepare(text: str) -> str:
    """Kannada digits -> ASCII, so numbers match one pattern."""
    return text.translate(_KANNADA_DIGITS)


def flatten(text: str) -> str:
    """Whole text on one line, for phrases that wrap across lines."""
    return re.sub(r"\s+", " ", text).strip()


def tidy(value: str | None) -> str | None:
    if not value:
        return None

    value = re.sub(r"\s+", " ", value).strip(" ,;:-")

    return value or None


def title_name(name: str) -> str:
    """'FAIROZA H. UKKALI' -> 'Fairoza H. Ukkali' (mixed case is kept)."""
    if name.upper() != name:
        return name

    return " ".join(
        word if re.fullmatch(r"[A-Z]\.?", word) else word.capitalize()
        for word in name.split()
    )


def paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


# ==========================================================
# 1. FILE NO.
# ==========================================================

_FILE_NO = re.compile(
    r"(?:\bfile|ಕಡತ)\s*(?:No|Number|ಸಂಖ್ಯೆ)\.?\s*[:.\-]?\s*"
    r"(?P<v>[A-Z0-9][A-Z0-9 /\-().]{2,50}?)"
    r"(?=\s+(?:received|dated|dt|from|of|on|ಸ್ವೀಕರಿಸಿದ|ದಿನಾಂಕ)\b|[,;\n]|\s{2}|$)",
    I,
)


def file_no(text: str) -> str | None:
    match = _FILE_NO.search(text)
    return tidy(match.group("v")) if match else None


# ==========================================================
# 2. GOVT. ORDER NO. & DATE
# ==========================================================

_GO_NO = re.compile(
    r"(?:GOVERNMENT\s+ORDER\s+N[O0]|G\.?\s*O\.?\s*(?:\(?Ms\.?\)?\s*)?N[O0]"
    r"|ಸರ್ಕಾರಿ\s+ಆದೇಶ\s+ಸಂಖ್ಯೆ|ಸರ್ಕಾರದ\s+ಆದೇಶ\s+ಸಂಖ್ಯೆ)"
    r"\.?\s*[:.\-]?\s*"
    r"(?P<v>[^\n,]+?)"
    r"(?=\s*(?:,|\n|\bBENGALURU\b|\bBANGALORE\b|\bDATED\b|\bdt\b|ಬೆಂಗಳೂರು|ದಿನಾಂಕ|$))",
    I,
)

_DATED = re.compile(
    rf"(?:\bDATED|\bdt|ದಿನಾಂಕ)\.?\s*[:.\-]?\s*(?P<d>{DATE})",
    I,
)


def go_number_and_date(text: str) -> tuple[str | None, str | None]:
    number_match = _GO_NO.search(text)
    number = tidy(number_match.group("v")) if number_match else None

    # The GO's own date follows its number ("BENGALURU, DATED:07.07.2025").
    start = number_match.end() if number_match else 0
    date_match = _DATED.search(text, start, start + 150) or _DATED.search(text)

    date = None

    if date_match:
        date = re.sub(r"\s*([./-])\s*", r"\1", date_match.group("d")).strip()

    return number, date


# ==========================================================
# 3. CASE NO.
# ==========================================================

_CASE_TYPES = (
    r"W\.?\s*P\.?\s*H\.?\s*C|W\.?\s*P|W\.?\s*A|C\.?\s*C\.?\s*C|R\.?\s*F\.?\s*A"
    r"|R\.?\s*S\.?\s*A|M\.?\s*F\.?\s*A|C\.?\s*R\.?\s*P|Crl\.?\s*R\.?\s*P"
    r"|Crl\.?\s*P|Crl\.?\s*A|S\.?\s*L\.?\s*P|O\.?\s*S|C\.?\s*P|R\.?\s*P"
    r"|Writ\s+Petition|Writ\s+Appeal|Contempt\s+Petition|Review\s+Petition"
)

_CASE_NO = re.compile(
    rf"\b(?P<type>{_CASE_TYPES})\.?\s*(?:\(\s*[A-Z\-/ ]+\s*\)\s*)?"
    r"No[s]?\.?\s*[:.]?\s*(?P<num>\d+)\s*/\s*(?P<year>(?:19|20)\d{2})",
    I,
)

_CASE_NO_KN = re.compile(
    r"(?P<type>ರಿಟ್\s*(?:ಅರ್ಜಿ|ಮೇಲ್ಮನವಿ)|ರಿಟ್\s*ಪಿಟಿಷನ್)\s*ಸಂಖ್ಯೆ\s*[:.\-]?\s*"
    r"(?P<num>\d+)\s*/\s*(?P<year>(?:19|20)\d{2})",
)


def case_number(text: str) -> str | None:
    found: list[str] = []

    for match in list(_CASE_NO.finditer(text)) + list(_CASE_NO_KN.finditer(text)):
        case_type = re.sub(r"\s+", "", match.group("type"))

        if re.fullmatch(r"[A-Za-z.]+", case_type):
            label = f"{case_type.rstrip('.')}.No."
        else:
            label = f"{tidy(match.group('type'))} ಸಂಖ್ಯೆ"

        value = f"{label} {match.group('num')}/{match.group('year')}"

        if value not in found:
            found.append(value)

    return ", ".join(found) or None


# ==========================================================
# 4 + 5. PETITIONER / RESPONDENT
# ==========================================================

_PARTIES = re.compile(
    r"(?:filed|preferred|instituted)\s+by\s+(?:the\s+petitioners?\s+)?"
    r"(?P<pet>.+?)\s+(?:V\s*/\s*S|Vs|Versus|V|against)\.?\s+"
    r"(?P<resp>.+?)"
    r"(?=\s+(?:on\s+the\s+file|on\s+the\s+Hon|before\s+the|in\s+the\s+Hon|pending)"
    r"|[.;]\s|$)",
    I,
)

_REPRESENTED_HEADER = re.compile(
    r"on\s+behalf\s+of\s+the\s+following\s+Respondents?", I
)

_REPRESENTED_INLINE = re.compile(
    r"on\s+behalf\s+of\s+(?:the\s+)?(?P<v>Respondents?\s+No[s]?\.?\s*"
    r"\d+(?:\s*(?:,|&|and|to)\s*\d+)*)",
    I,
)

_ITEM = re.compile(r"^\s*\(?(\d{1,2})\s*[).]\s*(.*)$")


def parties(text: str) -> tuple[str | None, str | None]:
    match = _PARTIES.search(flatten(text))

    if not match:
        return None, None

    return tidy(match.group("pet")), tidy(match.group("resp"))


def respondents_represented(text: str) -> list[str]:
    """Numbered respondents listed after 'on behalf of the following Respondent'."""
    header = _REPRESENTED_HEADER.search(text)

    if not header:
        inline = _REPRESENTED_INLINE.search(flatten(text))
        return [tidy(inline.group("v"))] if inline else []

    items: list[str] = []
    current: list[str] | None = None
    blank_after_item = False

    for line in text[header.end():].splitlines()[1:]:
        if not line.strip():
            if current is not None:
                blank_after_item = True
            continue

        item = _ITEM.match(line)

        if item:
            if current is not None:
                items.append(tidy(" ".join(current)))
            current = [item.group(2)]
            blank_after_item = False
        elif (current is not None and current[-1].rstrip().endswith(".")
              and line[:1].isupper()):
            break  # OCR without blank lines: a new sentence ends the list
        elif current is not None and not blank_after_item:
            current.append(line)
        elif current is not None:
            break  # a new paragraph after the list ends it
        elif items or len(line) > 3:
            break

    if current is not None:
        items.append(tidy(" ".join(current)))

    return [item.rstrip(".") for item in items if item]


# ==========================================================
# 6. GOVERNMENT ADVOCATE
# ==========================================================

_GA_ROLE = (
    r"(?:(?:Additional|Addl\.?|Senior|Special|Deputy|Asst\.?|Assistant)\s+)*"
    r"(?:Government\s+(?:Advocate|Pleader)|Advocate\s+General"
    r"|State\s+Public\s+Prosecutor|Public\s+Prosecutor)"
)

_GA = re.compile(
    r"(?P<ga>(?:(?:Sri|Smt|Shri|Kum|Dr)\.?\s+[A-Z][A-Za-z. ]{2,40}?,\s+)?"
    rf"{_GA_ROLE}.{{0,160}}?)"
    r"\s+(?:is|are)\s+(?:hereby\s+)?"
    r"(?:authori[sz]ed|directed|permitted|instructed|requested)\s+to\s+appear",
    I,
)


def government_advocate(text: str) -> str | None:
    match = _GA.search(flatten(text))
    return tidy(match.group("ga")) if match else None


# ==========================================================
# 7. LITIGATION CONDUCTING OFFICER (+ SWEARING OFFICER)
# ==========================================================

_LCO_PHRASE = re.compile(
    r"\s+(?:shall\s+be|is\s+(?:hereby\s+)?(?:appointed|nominated|designated)\s+as)"
    r"\s+(?:the\s+)?Litigation\s+Conducting\s+Officer"
    r"(?P<sw>\s*(?:and|&)\s*(?:the\s+)?Swearing\s+Officer)?",
    I,
)

_SWEARING = re.compile(
    r"(?:^|(?<=\.\s))(?P<v>[^.]{5,250}?)\s+shall\s+be\s+the\s+Swearing\s+Officer",
    I,
)

_PHONE = re.compile(
    r"(?:Phone|Ph|Tel|Telephone|Mobile|Mob|ದೂರವಾಣಿ)\.?\s*(?:No\.?)?\s*[:.\-]?\s*"
    r"(?P<v>\+?\d[\d\-\s/]{5,}\d)",
    I,
)

_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")

_CONTACT_BRACKET = re.compile(
    r"\((?=[^)]*(?:Phone|\bPh\b|Mob|Tel|E-?mail|@))[^)]*\)", I
)

# Sentence end: ". " + capital, but not after abbreviations like No. / Dr.
_SENTENCE_END = re.compile(
    r"(?<!\bNo)(?<!\bDr)(?<!\bSri)(?<!\bSmt)(?<!\bM\.S)(?<!\b[A-Z])\.\s+(?=[A-Z])"
)


def litigation_officer(text: str) -> LitigationOfficer:
    result = LitigationOfficer()

    for paragraph in paragraphs(text):
        flat = flatten(paragraph)
        match = _LCO_PHRASE.search(flat)

        if not match:
            continue

        before = flat[:match.start()]

        phone = _PHONE.search(before)
        email = _EMAIL.search(before)
        result.phone = tidy(phone.group("v")) if phone else None
        result.email = email.group(0) if email else None

        # Drop the "(Phone No... Email Id...)" part (but keep brackets that
        # belong to the title, like "(University-2)"), then keep only the
        # sentence that names the officer.
        officer = _CONTACT_BRACKET.sub(" ", before)
        officer = _SENTENCE_END.split(officer)[-1]

        result.officer = tidy(officer)
        result.is_swearing_officer = bool(match.group("sw"))
        break

    if not result.is_swearing_officer:
        swearing = _SWEARING.search(flatten(text))

        if swearing:
            result.swearing_officer = tidy(
                _CONTACT_BRACKET.sub(" ", swearing.group("v"))
            )

    return result


# ==========================================================
# 8. ISSUING AUTHORITY
# ==========================================================

_SIGN_OFF = re.compile(
    r"(?:IN\s+THE\s+NAME\s+OF\s+THE\s+GOVERNOR\s+OF\s+KARNATAKA"
    r"|ರಾಜ್ಯಪಾಲರ\s+ಆದೇಶಾನುಸಾರ[^\n]*)",
    I,
)

_END_OF_SIGNATURE = re.compile(
    r"^(?:To\b|To:|ಗೆ\b|ಇವರಿಗೆ|Copy\s+to|ಪ್ರತಿ|\(?\d+\s*[).])",
    I,
)


def issuing_authority(text: str) -> IssuingAuthority:
    sign_off = _SIGN_OFF.search(text)

    if not sign_off:
        return IssuingAuthority()

    lines = [line.strip() for line in text[sign_off.end():].splitlines()]
    lines = [line for line in lines if line]

    name = None
    designation: list[str] = []

    for line in lines[:8]:
        if _END_OF_SIGNATURE.match(line):
            break

        if name is None:
            bracketed = re.search(r"\(([^)]{3,60})\)", line)

            if bracketed:
                name = bracketed.group(1)
                continue

            # A signature scrawl OCR'd as junk: skip short noise lines.
            if len(re.sub(r"[^A-Za-zಀ-೿]", "", line)) < 4:
                continue

            name = line
            continue

        # The name read a second time from the signature: "(FAIROZA H. UKNALI)"
        if not designation and re.fullmatch(r"\([^)]{3,60}\)", line):
            continue

        designation.append(line)

    return IssuingAuthority(
        name=title_name(tidy(name)) if name else None,
        designation=tidy(" ".join(designation)),
    )


# ==========================================================
# ALL FIELDS
# ==========================================================


def extract_advocate(text: str) -> GOExtraction:
    text = prepare(text)

    go_number, go_date = go_number_and_date(text)
    petitioner, respondent = parties(text)

    result = GOExtraction(
        file_no=file_no(text),
        go_number=go_number,
        go_date=go_date,
        case_number=case_number(text),
        petitioner=petitioner,
        respondent=respondent,
        respondents_represented=respondents_represented(text),
        government_advocate=government_advocate(text),
        litigation_officer=litigation_officer(text),
        issuing_authority=issuing_authority(text),
    )

    for name, value in result.to_dict().items():
        if name == "sources":
            continue
        if isinstance(value, dict):
            value = any(value.values())
        if value:
            result.sources[name] = "rules"

    return result


# ==========================================================================
# GOVERNMENT ADVOCATE GOs: LLM FALLBACK (was go_extractor/llm/fallback.py)
# LLM fallback: fill only the fields the rules left empty.
#
# Every value the model returns is checked against the document text and
# dropped if it does not appear there (fuzzy match, to tolerate small OCR
# differences). This keeps hallucinated names, numbers and dates out.
# ==========================================================================

# Minimum fuzzy similarity (0-100) between a value and the document text.
MIN_MATCH = 88

# Max characters of document text sent to the model (GOs are short).
ADVOCATE_MAX_TEXT_CHARS = 8000

ADVOCATE_FIELD_HELP = {
    "file_no": (
        "The value written right after the words 'file No.' (e.g. "
        "'With reference to the file No. ED 02 UTT 2025' -> 'ED 02 UTT 2025')"
    ),
    "go_number": "Government Order number (e.g. 'ED 02 UTT 2025')",
    "go_date": "Date of the Government Order, as written (e.g. '07.07.2025')",
    "case_number": "Court case number (e.g. 'W.P.No. 18329/2025')",
    "petitioner": "Name of the petitioner who filed the case",
    "respondent": "Respondent(s) as named in the case title",
    "government_advocate": (
        "The Government Advocate / Pleader authorised to appear, with court"
    ),
    "litigation_officer": (
        "Officer appointed as Litigation Conducting Officer (designation "
        "and office, without phone/email)"
    ),
    "issuing_authority": "Name of the officer who signed the order",
    "issuing_authority_designation": "Designation of the signing officer",
}


def fill_missing(
    result: GOExtraction,
    text: str,
    client: LLMClient,
) -> GOExtraction:
    missing = result.missing_fields()

    if "issuing_authority" in missing or not result.issuing_authority.designation:
        missing.append("issuing_authority_designation")

    missing = list(dict.fromkeys(missing))

    if not missing:
        return result

    reply = client.generate(_advocate_prompt(missing, text[:ADVOCATE_MAX_TEXT_CHARS]))
    answers = _parse(reply)

    for name in missing:
        value = answers.get(name)

        if not isinstance(value, str) or not value.strip():
            continue

        value = re.sub(r"\s+", " ", value).strip(" ,;:")

        if not _appears_in(value, text):
            logger.info("LLM value for %s not found in text: %r", name, value)
            continue

        _advocate_set(result, name, value)
        result.sources[name.replace("_designation", "")] = "llm"

    return result


def _advocate_prompt(fields: list[str], text: str) -> str:
    keys = "\n".join(f'- "{name}": {ADVOCATE_FIELD_HELP[name]}' for name in fields)
    example = json.dumps({name: "..." for name in fields})

    return f"""The text below is a Karnataka Government Order (English and/or Kannada) authorising a Government Advocate to appear in a court case.

Extract these fields:
{keys}

Rules:
- Copy values exactly as written in the text. Do not translate or rephrase.
- Use null for a field that is not in the text.

Text:
{text}

Return one JSON object with exactly these keys: {example}"""


def _parse(reply: str) -> dict:
    try:
        data = json.loads(reply)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    return data if isinstance(data, dict) else {}


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _appears_in(value: str, text: str) -> bool:
    value, text = _normalise(value), _normalise(text)

    if value in text:
        return True

    return partial_ratio(value, text, score_cutoff=MIN_MATCH) >= MIN_MATCH


def _advocate_set(result: GOExtraction, name: str, value: str) -> None:
    if name == "litigation_officer":
        result.litigation_officer.officer = value
    elif name == "issuing_authority":
        result.issuing_authority.name = value
    elif name == "issuing_authority_designation":
        result.issuing_authority.designation = value
    else:
        setattr(result, name, value)


# ==========================================================================
# SUSPENSION ORDERS (was go_extractor/suspension.py)
# Suspension orders (Kannada / English): orders that suspend a government
# servant, extend a suspension, or revoke it and reinstate them.
#
# Their wording differs by department (police, panchayat, forest, transport,
# education, land records ...), so the LLM reads the whole order and every
# value it copies is checked against the OCR text, as in llm/fallback.py.
# Dates are normalised to DD.MM.YYYY and must match a date in the text.
# ==========================================================================

# Suspension orders run to 4 pages; the model has a 32k context.
SUSPENSION_MAX_TEXT_CHARS = 16000

SUSPENSION_TYPES = (
    "Suspension",
    "Suspension extended",
    "Suspension revoked",
    "Other (suspension related)",
)

IMMEDIATE = "With immediate effect"

# ಅಮಾನತು / ಅಮಾನತ್ತು / ಅಮಾನತ್ತಿನಲ್ಲಿಡುವ ... all start with ಅಮಾನತ.
_SUSPENSION_WORDS = re.compile(r"ಅಮಾನತ|\bsuspen(?:sion|ded|d)\b", re.I)
_KANNADA = re.compile(r"[\u0C80-\u0CFF]")
_ADVOCATE_WORDS = re.compile(
    r"authori[sz]ed to appear|Government Advocate|Litigation Conducting",
    re.I,
)

# The subject line (ವಿಷಯ) states what the order does; checked in this order
# because "pay during suspension" also contains the word ಅಮಾನತ.
_SUBJECT = re.compile(
    r"ವಿಷಯ\s*[:;ಃ-]*(.{0,400}?)(?:ಉಲ್ಲೇ|ಓದಲಾಗಿದೆ|ಉಪಸ್ಥಿತ|ಪ್ರಸ್ತಾವನೆ|\*\*)",
    re.S,
)
_SUBJECT_TYPES = (
    ("Suspension revoked", re.compile(r"ತೆರವು|ಪುನರ್|ಹಿಂಪಡೆ|ರದ್ದು")),
    ("Suspension extended", re.compile(r"ಮುಂದುವರೆ|ಮುಂದುವರಿ|ವಿಸ್ತರ")),
    # ... or relieves them from duty (ಕರ್ತವ್ಯದಿಂದ ಬಿಡುಗಡೆ).
    ("Other (suspension related)",
     re.compile(r"ವೇತನ|ಜೀವನಾಧಾರ|ಲೀನ್|ಲಿಯನ್|ಕರ್ತವ್ಯದಿಂದ\s*ಬಿಡುಗಡೆ")),
    ("Suspension", re.compile(r"ಅಮಾನತ")),
)

# Words that make an "issuing authority name" a post, not a person.
# ("ಧಿಕಾರಿ" also catches compounds such as ಕಲ್ಯಾಣಾಧಿಕಾರಿ.)
_POST_WORDS_EN = re.compile(
    r"\b(officer|commissioner|superintendent|secretary|director|"
    r"inspector|authority|department|office)\b",
    re.I,
)

_POST_WORDS = re.compile(
    r"ಅಧೀಕ್ಷಕ|ಧಿಕಾರಿ|ಪ್ರಾಧಿಕಾರ|ರಾಜ್ಯಪಾಲ|ಆಯುಕ್ತ|ಕಾರ್ಯದರ್ಶಿ|ನಿರ್ದೇಶಕ|"
    r"ಇಲಾಖೆ|ಕಛೇರಿ|ಕಚೇರಿ|ಜಿಲ್ಲೆ|ಪಂಚಾಯತ್"
)

# Service after a signer's name: ", ಭಾ.ಪೊ.ಸೇ." / ", ಐ.ಪಿ.ಎಸ್" / ", IPS" /
# ", Dr. P. S." (an OCR misreading of ಭಾ.ಪೊ.ಸೇ. put into English).
_SERVICE = re.compile(
    r",\s*(?:(?:\S{1,4}\.\s*)+\S{0,4}|I\.?A\.?S|I\.?P\.?S|K\.?A\.?S)\.?\s*$",
    re.I,
)

SUSPENSION_FIELD_HELP = {
    "order_type": (
        f"One of {', '.join(repr(t) for t in SUSPENSION_TYPES)}, for the main "
        "order. 'Suspension' = it suspends the employee (ಅಮಾನತ್ತಿನಲ್ಲಿಡುವ / "
        "ಅಮಾನತ್ತುಗೊಳಿಸುವ / ಅಮಾನತ್ತಿನಲ್ಲಿರಿಸಿ). 'Suspension extended' = it "
        "continues an existing suspension for a further period "
        "(ಮುಂದುವರೆಸುವ / ವಿಸ್ತರಿಸುವ). 'Suspension revoked' = it ends the "
        "suspension and reinstates the employee (ತೆರವುಗೊಳಿಸಿ / "
        "ಪುನರ್‌ಸ್ಥಾಪಿಸುವ / ಹಿಂಪಡೆದು). 'Other (suspension related)' = an "
        "order about an employee who is already suspended that does none "
        "of these (pay or subsistence allowance, lien transfer, relieving)"
    ),
    "order_number": (
        "Number of the main order, as written (after ಸಂಖ್ಯೆ / ಸಂ: at the "
        "top or under the ಆದೇಶ heading). Not the number of a letter in the "
        "references (ಉಲ್ಲೇಖ / ಓದಲಾಗಿದೆ)"
    ),
    "order_date": (
        "Date of the main order: the ದಿನಾಂಕ next to its number or at its "
        "signature. Never the date of a referenced letter or of an event. "
        "null if that date is handwritten or unreadable"
    ),
    "employee_name": (
        "Name of the employee the order is about, without ಶ್ರೀ / ಶ್ರೀಮತಿ"
    ),
    "employee_designation": "That employee's post (job title)",
    "employee_office": "Office / police station / school where they work",
    "reason": (
        "One short sentence IN ENGLISH, in your own words, saying what this "
        "employee is accused of in this document. Base it only on this "
        "text"
    ),
    "rule_invoked": (
        "The rule and rules book under which the main order is passed "
        "(e.g. the ನಿಯಮ 10(...) of the ಕರ್ನಾಟಕ ನಾಗರಿಕ ಸೇವಾ (ವರ್ಗೀಕರಣ, "
        "ನಿಯಂತ್ರಣ ಮತ್ತು ಮೇಲ್ಮನವಿ) ನಿಯಮಗಳು 1957)"
    ),
    "case_reference": (
        "Number of the criminal / Lokayukta / police case against the "
        "employee (ಮೊ.ಸಂ / ಅಪರಾಧ ಸಂಖ್ಯೆ / ಪ್ರಕರಣ ಸಂಖ್ಯೆ), only the first "
        "one if there are several. null if none"
    ),
    "effective_from": (
        "Date from which the main order takes effect, or "
        f"'{IMMEDIATE}' if it says ತಕ್ಷಣದಿಂದ / immediately"
    ),
    "issuing_authority": (
        "Personal name of the officer who signed the main order (often "
        "in brackets under the signature, or 'X ... ಆದ ನಾನು'). Only a "
        "person's name, never a post, office or department. null if no "
        "name is printed"
    ),
    "issuing_authority_designation": (
        "Post of that officer, printed under the signature (e.g. ಪೊಲೀಸ್‌ "
        "ಅಧೀಕ್ಷಕರು, ಗದಗ ಜಿಲ್ಲೆ). Not a rule, not a qualification such as "
        "ಭಾ.ಪೊ.ಸೇ. / ಐ.ಪಿ.ಎಸ್"
    ),
}

# Fields copied from the document (checked against the text).
_SUSPENSION_COPIED = (
    "order_number", "employee_name", "employee_designation",
    "employee_office", "rule_invoked", "case_reference",
    "issuing_authority", "issuing_authority_designation",
)
_DATES = ("order_date", "effective_from")

ENGLISH_HELP = (
    "English version of the value: personal names and place names "
    "transliterated into English letters (ಸಿ.ವಿ.ಇಂಗಳಹಳ್ಳಿ -> "
    "C.V. Ingalahalli), posts, offices, departments and rules translated "
    "into standard English (ಪೊಲೀಸ್ ಅಧೀಕ್ಷಕರು, ಗದಗ ಜಿಲ್ಲೆ -> "
    "Superintendent of Police, Gadag District; ಎಎಸ್‌ಐ -> ASI; "
    "ಮೆಟ್ರಿಕ್ ಪೂರ್ವ ಬಾಲಕರ ವಿದ್ಯಾರ್ಥಿನಿಲಯ -> Pre-Matric Boys' Hostel; "
    "ಹಿಂದುಳಿದ ವರ್ಗಗಳ ಕಲ್ಯಾಣ ಇಲಾಖೆ -> Backward Classes Welfare "
    "Department), numbers "
    "and codes kept with their digits. Same as the value if it is "
    "already in English"
)


# ==========================================================
# PUBLIC
# ==========================================================


def is_suspension_order(text: str) -> bool:
    """True for suspension orders, False for Government Advocate GOs."""
    if _ADVOCATE_WORDS.search(text):
        return False

    return bool(_SUSPENSION_WORDS.search(text))


def extract_suspension(
    text: str,
    client: LLMClient,
    pages: list[str] | None = None,
) -> SuspensionOrder:
    result = SuspensionOrder()
    reply = client.generate(_suspension_prompt(_with_page_marks(text, pages)))
    answers = _parse(reply)

    if not answers:
        raise ValueError(f"LLM reply was not JSON: {reply[:200]!r}")

    dates = _dates_in(text)

    for name in SUSPENSION_FIELD_HELP:
        value = answers.get(name)

        if not isinstance(value, str) or not value.strip():
            continue

        value = re.sub(r"\s+", " ", value).strip(" ,;:")

        if "\ufffd" in value:
            value = _repair(value, text)

        original = value

        if name == "order_type":
            value = _subject_type(text) or _order_type(value)
        elif name == "issuing_authority" and (
            _POST_WORDS.search(value)
            or _POST_WORDS_EN.search(str(answers.get(f"{name}_en") or ""))
        ):
            logger.info("LLM issuing authority is a post: %r", value)
            value = None
        elif name == "reason" and _KANNADA.search(value):
            logger.info("LLM reason not in English: %r", value)
            value = None
        elif name in _DATES:
            value = _date(value, dates)
        elif name in _SUSPENSION_COPIED and not _appears_in(value, text):
            logger.info("LLM value for %s not found in text: %r", name, value)
            value = None

        if not value:
            continue

        if name in _SUSPENSION_COPIED:
            result.original[name] = original
            value = _english(answers.get(f"{name}_en"), original)

        if name == "issuing_authority":
            result.original[name] = _SERVICE.sub("", original)
            value = _SERVICE.sub("", value)

        _suspension_set(result, name, value)
        result.sources[name.replace("_designation", "")] = (
            "llm-summary" if name == "reason" else "llm"
        )

    return result


def _english(value, original: str) -> str:
    """
    The model's English version of a checked value. Only used when the
    Kannada value passed the text check; kept in Kannada if the English
    is missing or still in Kannada script.
    """
    if not isinstance(value, str) or not value.strip():
        return original

    value = re.sub(r"\s+", " ", value).strip(" ,;:")

    if _KANNADA.search(value) or "�" in value:
        logger.info("LLM English value not usable: %r", value)
        return original

    return value


def _subject_type(text: str) -> str | None:
    """Order type from the subject line, when it says so plainly."""
    match = _SUBJECT.search(text)

    if not match:
        return None

    for order_type, words in _SUBJECT_TYPES:
        if words.search(match.group(1)):
            return order_type

    return None


# ==========================================================
# PROMPT
# ==========================================================


def _with_page_marks(text: str, pages: list[str] | None) -> str:
    """Text with "=== Page N ===" lines, so the model sees page 1."""
    if not pages:
        return text[:SUSPENSION_MAX_TEXT_CHARS]

    marked = "\n\n".join(
        f"=== Page {number} ===\n{page}"
        for number, page in enumerate(pages, start=1) if page.strip()
    )
    return marked[:SUSPENSION_MAX_TEXT_CHARS]


def _suspension_prompt(text: str) -> str:
    fields = {}

    for name, help in SUSPENSION_FIELD_HELP.items():
        fields[name] = help

        if name in _SUSPENSION_COPIED:
            fields[f"{name}_en"] = ENGLISH_HELP

    keys = "\n".join(f'- "{name}": {help}' for name, help in fields.items())
    example = json.dumps({name: "..." for name in fields})

    return f"""The text below is an OCR scan of a Karnataka government order (Kannada and/or English) about the suspension of a government employee. OCR may have small errors.

Extract these fields:
{keys}

Rules:
- The main order is the newest order this document issues; it starts on page 1. Later pages may be copies of earlier orders it refers to: do not take the order number, date or type from those.
- Copy values exactly as written in the text, in Kannada script when the text is in Kannada. Never translate or transliterate them into Hindi / Devanagari. Each "..._en" key holds the English version of the key before it; "order_type" and "reason" are in English.
- Copy one continuous phrase from the text for each value; do not join several phrases.
- Write dates as DD.MM.YYYY.
- Use null for a field that is not in the text.

Text:
{text}

Return one JSON object with exactly these keys: {example}"""


# ==========================================================
# VALIDATION
# ==========================================================


_IMMEDIATE_WORDS = re.compile(r"ತಕ್ಷಣ|immediate", re.I)
IMMEDIATE_KEY = (0, 0, 0)

_DATE = re.compile(r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})\b")


def _date_key(day: str, month: str, year: str) -> tuple[int, int, int]:
    year = int(year)
    return int(day), int(month), year + 2000 if year < 100 else year


def _dates_in(text: str) -> set[tuple[int, int, int]]:
    """
    Every DD.MM.YYYY-like date in the text (Kannada digits included), and
    IMMEDIATE_KEY if the text says "with immediate effect" (ತಕ್ಷಣ).
    """
    dates = {_date_key(*m.groups()) for m in _DATE.finditer(prepare(text))}

    if _IMMEDIATE_WORDS.search(text):
        dates.add(IMMEDIATE_KEY)

    return dates


def _date(value: str, dates: set[tuple[int, int, int]]) -> str | None:
    """DD.MM.YYYY if the date occurs in the text, else None."""
    if _IMMEDIATE_WORDS.search(value):
        # Only when the order itself says so.
        return IMMEDIATE if IMMEDIATE_KEY in dates else None

    match = _DATE.search(prepare(value))

    if not match:
        return None

    key = _date_key(*match.groups())

    if key not in dates:
        logger.info("LLM date not found in text: %r", value)
        return None

    day, month, year = key
    return f"{day:02d}.{month:02d}.{year}"


def _repair(value: str, text: str) -> str:
    """
    The LLM server sometimes returns U+FFFD for a Kannada letter (seen at
    the start of values: "ಎ7/..." -> "\ufffd7/..."). Put back the
    characters from the text the value was copied from.
    """
    parts = [re.escape(part) for part in value.split("\ufffd")]
    pattern = r"\S{1,2}?".join(parts).replace(r"\ ", r"\s+")
    match = re.search(pattern, text)

    return match.group(0) if match else value.replace("\ufffd", "")


def _order_type(value: str) -> str | None:
    for order_type in SUSPENSION_TYPES:
        if value.lower() == order_type.lower():
            return order_type

    lowered = value.lower()

    if "revok" in lowered or "reinstat" in lowered:
        return "Suspension revoked"
    if "exten" in lowered or "continu" in lowered:
        return "Suspension extended"
    if lowered.startswith("other"):
        return "Other (suspension related)"
    if "suspen" in lowered:
        return "Suspension"

    return None


def _suspension_set(result: SuspensionOrder, name: str, value: str) -> None:
    if name == "issuing_authority":
        result.issuing_authority.name = value
    elif name == "issuing_authority_designation":
        result.issuing_authority.designation = value
    else:
        setattr(result, name, value)


# ==========================================================================
# HRMS2 DATA ELEMENTS (was go_extractor/data_elements.py)
# HRMS2 disciplinary / suspension data elements
# (HRMS2_Disciplinary_Suspension_Data_Elements_Ver2.0.xlsx).
#
# Each order type gets the Common and Disciplinary elements (applies to
# ALL) plus the elements marked for it. Values are extracted only when the
# document states them; anything not present stays blank, never inferred.
#
# To add an order type: add its elements below with
# applies_to=(<order type>,), map it in ORDER_TYPES and describe it in
# ORDER_DESCRIPTIONS.
# ==========================================================================

ALL = "ALL"
INVOKE = "Suspension Invoke Order"
EXTEND = "Suspension Extend Order"
REVOKE = "Suspension Revoke Order"

# Order type from suspension.py -> data-element order type. Suspension
# orders whose type is not listed ("Other (suspension related)") keep the
# fields in suspension.py.
ORDER_TYPES = {
    "Suspension": INVOKE,
    "Suspension extended": EXTEND,
    "Suspension revoked": REVOKE,
}

# The order type is taken from the file name when it says it
# ("Suspension_Invoke_Orders_739522.pdf"); the content is not used for it.
FILENAME_TYPES = (
    (re.compile(r"invoke", re.I), INVOKE),
    (re.compile(r"exten", re.I), EXTEND),
    (re.compile(r"revoke|revocation", re.I), REVOKE),
)


def type_from_filename(name: str) -> str | None:
    """Data-element order type named in the file name, if any."""
    for words, order_type in FILENAME_TYPES:
        if words.search(name):
            return order_type

    return None


# What each order does, for the LLM prompt.
ORDER_DESCRIPTIONS = {
    INVOKE: "places a government employee under suspension",
    EXTEND: "continues (extends) the suspension of a government employee "
            "who is already suspended",
    REVOKE: "revokes the suspension of a government employee and "
            "reinstates them in service",
}

# Elements that belong to one employee. An order about several employees
# gives one set of these per employee (one Excel row each); every other
# element is the same for the whole order.
EMPLOYEE_KEYS = frozenset({
    "employee_name", "designation", "office_name", "ddo_code",
    "employee_kgid", "dgo_kgid", "cadre", "current_posting",
    "lien_post", "reinstatement_posting_office",
    "reinstatement_posting_office_address",
})

# Checked in code, not left to the model (see order_elements.py).
# Elements kept only when the document uses these words: a Yes / No or a
# charge-sheet number is not accepted from an order that never mentions it.
EVIDENCE = {
    "subsistence_allowance_applicable":
        r"ಜೀವನಾಧಾರ|ಜೀವನ\s*ಭತ್ಯೆ|subsistence",
    "subsistence_allowance": r"ಜೀವನಾಧಾರ|ಜೀವನ\s*ಭತ್ಯೆ|subsistence",
    "charge_given_during_suspension":
        r"ದೋಷಾರೋಪಣ|ಆರೋಪಣ[ಾ]?\s*ಪಟ್ಟಿ|ಆರೋಪ\s*ಪಟ್ಟಿ|ಚಾರ್ಜ್\s*ಶೀಟ್|"
        r"charge\s*-?\s*(sheet|memo)",
    "charge_sheet_no":
        r"ದೋಷಾರೋಪಣ|ಆರೋಪಣ[ಾ]?\s*ಪಟ್ಟಿ|ಆರೋಪ\s*ಪಟ್ಟಿ|ಚಾರ್ಜ್\s*ಶೀಟ್|"
        r"charge\s*-?\s*(sheet|memo)",
    "lien_post": r"ಲೀನ್|ಲಿಯನ್|\blien\b",
    # The suspension itself continued ("ಮುಂದುವರೆಸಿ", continue *it*) or
    # extended, within a few words: not "ನಿಯಂತ್ರಣದಲ್ಲಿ ಮುಂದುವರೆಯುತ್ತೀರಿ"
    # (*you* continue under the same officer).
    "extension_required":
        r"ಅಮಾನತ್ತ?\S*(?:\s+\S+){0,4}?\s+(?:ಮುಂದುವರ[ೆಿ]ಸ|ವಿಸ್ತರ)|"
        r"suspension(?:\s+\S+){0,4}?\s+(?:extend|continu)|"
        r"(?:extension|continuation)\s+of\s+(?:the\s+)?suspension",
    # Only when the order says it was approved: not the signer / its date.
    "approved_by": r"ಅನುಮೋದ|approv",
    "approval_date": r"ಅನುಮೋದ|approv",
    # Only an authority the order calls competent / disciplinary, not
    # whoever signed it.
    "competent_authority":
        r"ಸಕ್ಷಮ|ಶಿಸ್ತು\s*ಪ್ರಾಧಿಕಾರ|competent|disciplinary\s*authorit",
}

# Identifiers: the value must follow its label in the text (within a few
# words), so a vehicle, constable or handwritten number is not taken for
# a KGID.
LABELS = {
    "employee_kgid": r"ಕೆ\.?\s*ಜಿ\.?\s*ಐ\.?\s*ಡಿ|K\.?\s*G\.?\s*I\.?\s*D",
    "dgo_kgid": r"ಕೆ\.?\s*ಜಿ\.?\s*ಐ\.?\s*ಡಿ|K\.?\s*G\.?\s*I\.?\s*D",
    "ddo_code": r"ಡಿ\.?\s*ಡಿ\.?\s*ಒ|ಡಿ\.?\s*ಡಿ\.?\s*ಓ|D\.?\s*D\.?\s*O",
}

# Dates that must be printed near these words (within NEAR_WINDOW
# characters after the date), so the incident or a report date is not
# taken for the date the case was registered.
NEAR = {
    "case_registration_date": r"ದಾಖಲ|regist",
    # A date "from which" it takes effect, not just the order's date.
    "reinstatement_date": r"ರಿಂದ|ಜಾರಿ|from|effect",
    "suspension_effective_to": r"ರಿಂದ|ಜಾರಿ|ವರೆಗೆ|from|effect|till|until",
}
NEAR_WINDOW = 150

# Values that must not be the number printed after these labels: the
# order book number (ಓಬಿ ಸಂಖ್ಯೆ) is not a case number.
NOT_AFTER = {
    "disciplinary_case_no": r"ಓ\.?\s*ಬಿ|\bO\.?\s*B\b",
}

# Kinds of value, each checked differently (see order_elements.py):
#   text   copied from the document, given in English too
#   list   several items copied from the document (references, copy to)
#   date   DD.MM.YYYY, must be a date in the document
#   yes_no "Yes" / "No", only when the document says so
#   label  chosen by the model from the description (document type)


@dataclass(frozen=True)
class DataElement:
    number: int
    key: str
    group: str
    label: str
    category: str
    description: str
    applies_to: tuple[str, ...] = (ALL,)
    kind: str = "text"
    # Dates only: "With immediate effect" is also a valid value.
    immediate: bool = False
    # Extra guidance for the LLM (Kannada wording, what not to take).
    hint: str = ""

    @property
    def per_employee(self) -> bool:
        return self.key in EMPLOYEE_KEYS

    @property
    def evidence(self) -> str | None:
        return EVIDENCE.get(self.key)

    @property
    def label_words(self) -> str | None:
        return LABELS.get(self.key)

    @property
    def near_words(self) -> str | None:
        return NEAR.get(self.key)

    @property
    def not_after(self) -> str | None:
        return NOT_AFTER.get(self.key)


ELEMENTS = (
    # ---------- Common ----------
    DataElement(1, "document_type", "Document", "Document Type", "Common",
                "Notice / Order / Suspension Order / Charge Sheet / etc.",
                kind="label",
                hint="The kind of document this is, in English, e.g. "
                     "'Suspension Order'"),
    DataElement(2, "employee_name", "Employee", "Employee Name", "Common",
                "Auto-populated from Employee Master",
                hint="Name of the employee the order is about, without "
                     "ಶ್ರೀ / ಶ್ರೀಮತಿ / Sri / Smt"),
    DataElement(3, "designation", "Employee", "Designation", "Common",
                "Designation at the time of action",
                hint="That employee's post, as written right after the "
                     "employee's name (e.g. ಎಎಸ್‌ಐ, ಸಿ.ಪಿ.ಸಿ.16778, "
                     "ಎಪಿಸಿ-328, ಅಡುಗೆಯವರು). Not the unit or office "
                     "(e.g. ಡಿಎಆರ್), not the post of another officer named "
                     "in the order"),
    DataElement(4, "office_name", "Employee", "Office Name", "Common",
                "Employee's office",
                hint="Office / police station / school where the employee "
                     "works"),
    DataElement(5, "ddo_code", "Employee", "DDO Code", "Common",
                "Financial / Payroll linkage",
                hint="Drawing and Disbursing Officer code, only if a DDO "
                     "code is printed"),
    DataElement(6, "employee_kgid", "Employee", "Employee KGID", "Common",
                "Employee identifier",
                hint="KGID number of the employee, only a number the "
                     "text labels as KGID (ಕೆಜಿಐಡಿ / KGID No.)"),
    DataElement(7, "disciplinary_authority_name", "Authority",
                "Disciplinary Authority Name", "Common",
                "Authority issuing / initiating the action",
                hint="Name and post of the officer who signed / issued "
                     "this order (printed under the signature)"),
    DataElement(8, "order_no", "Document", "Order No.", "Common",
                "Order / document reference number",
                hint="Number of this order (after ಸಂಖ್ಯೆ / ಸಂ: / No. at "
                     "the top or under the ಆದೇಶ heading). Not the number of "
                     "a referenced letter"),
    DataElement(9, "order_date", "Document", "Order Date", "Common",
                "Date of order", kind="date",
                hint="Date of this order (the ದಿನಾಂಕ next to its number or "
                     "at its signature). Never the date of a referenced "
                     "letter or of an event"),
    DataElement(10, "reference", "Document", "Reference", "Common",
                "Reference(s) cited in the document", kind="list",
                hint="Each reference cited (under ಉಲ್ಲೇಖ / ಓದಲಾಗಿದೆ / Ref / "
                     "Read), one list item each"),
    DataElement(11, "subject", "Document", "Subject", "Common",
                "Subject / title of the document",
                hint="The subject line (after ವಿಷಯ / Sub), without the "
                     "word ವಿಷಯ"),
    DataElement(12, "copy_to", "Distribution", "Copy To", "Common",
                "Recipients / authorities to whom copy is sent",
                kind="list",
                hint="Each recipient under ಪ್ರತಿಯನ್ನು / ಪ್ರತಿ / Copy to, one "
                     "list item each"),

    # ---------- Disciplinary ----------
    DataElement(13, "disciplinary_case_no", "Case", "Disciplinary Case No.",
                "Disciplinary",
                "Unique disciplinary case / proceeding reference",
                hint="Number of the disciplinary case / proceeding / "
                     "departmental enquiry against the employee, or of the "
                     "criminal / Lokayukta case (ಪ್ರಕರಣ ಸಂಖ್ಯೆ / ಮೊ.ಸಂ / "
                     "ಅಪರಾಧ ಸಂಖ್ಯೆ) if that is the only case named. Not the "
                     "number of a report, letter or order in the "
                     "references, not this order's own number, not the "
                     "order book number (ಓಬಿ ಸಂಖ್ಯೆ)"),
    DataElement(14, "case_registration_date", "Case",
                "Case Registration Date", "Disciplinary",
                "Date the case was registered", kind="date",
                hint="Date that case was registered (ದಾಖಲಾದ / "
                     "ದಾಖಲಿಸಿದ ದಿನಾಂಕ), not the date of the incident"),
    DataElement(15, "case_type", "Case", "Case Type", "Disciplinary",
                "Type of disciplinary proceeding",
                hint="Type of case / proceeding as the order names it (e.g. "
                     "Lokayukta trap case, criminal case, departmental "
                     "enquiry)"),
    DataElement(16, "nature_of_disciplinary_action", "Case",
                "Nature of Disciplinary Action", "Disciplinary",
                "Nature / category of action",
                hint="The action this order takes, as the order words it "
                     "(e.g. placed under suspension pending enquiry)"),
    DataElement(17, "dgo_kgid", "Employee", "DGO KGID", "Disciplinary",
                "HRMS employee identifier",
                hint="KGID of the Delinquent Government Official, only a "
                     "number the text labels as KGID"),
    DataElement(18, "department", "Employee", "Department", "Disciplinary",
                "Employee's department",
                hint="Department the employee belongs to, as the order "
                     "names it (letterhead, office heading or text, e.g. "
                     "ಪೊಲೀಸ್ ಇಲಾಖೆ)"),
    DataElement(19, "cadre", "Employee", "Cadre", "Disciplinary",
                "Employee cadre",
                hint="Cadre / group of the employee's post, only if the "
                     "order states it (ವೃಂದ / Group A-D)"),
    DataElement(20, "current_posting", "Employee", "Current Posting",
                "Disciplinary", "Current posting / place of work",
                hint="Place where the employee is working, as the order "
                     "states it (often the same as the office)"),
    DataElement(21, "competent_authority", "Authority",
                "Competent Authority", "Disciplinary",
                "Authority competent to take action",
                hint="The authority the order names as competent / "
                     "appointing / disciplinary authority (ಸಕ್ಷಮ "
                     "ಪ್ರಾಧಿಕಾರ / ಶಿಸ್ತು ಪ್ರಾಧಿಕಾರಿಗಳು, often printed under "
                     "the signature)"),
    DataElement(22, "applicable_rule", "Legal", "Applicable Rule",
                "Disciplinary",
                "Applicable service / disciplinary rule",
                hint="Name of the rules book under which this order is "
                     "passed (e.g. ಕರ್ನಾಟಕ ನಾಗರಿಕ ಸೇವಾ (ವರ್ಗೀಕರಣ, ನಿಯಂತ್ರಣ "
                     "ಮತ್ತು ಮೇಲ್ಮನವಿ) ನಿಯಮಗಳು 1957). If the order names none, "
                     "the service / conduct rules it says the employee "
                     "broke (e.g. ಕರ್ನಾಟಕ ನಾಗರಿಕ ಸೇವಾ (ನಡತೆ) ನಿಯಮಗಳು 2021). "
                     "Never the pay rules for subsistence allowance "
                     "(ಕೆ.ಸಿ.ಎಸ್.ಆರ್ ನಿಯಮ 98)"),
    DataElement(23, "rule_section", "Legal", "Rule / Section",
                "Disciplinary", "Specific rule, section or provision",
                hint="The rule number / sub-rule of the Applicable Rule "
                     "that the order cites (e.g. ನಿಯಮ 10(1)(ಡಿ))"),
    DataElement(24, "approved_by", "Workflow", "Approved By",
                "Disciplinary", "Approving authority / user",
                hint="Who approved the order, only if the document says "
                     "it was approved by someone (ಅನುಮೋದನೆ)"),
    DataElement(25, "approval_date", "Workflow", "Approval Date",
                "Disciplinary", "Date of approval", kind="date",
                hint="Date of that approval, only if printed"),

    # ---------- Suspension Invoke Order ----------
    DataElement(26, "suspension_order_no", "Suspension",
                "Suspension Order No.", "Suspension",
                "Suspension order reference", applies_to=(INVOKE,),
                hint="Number of the order that suspends the employee; "
                     "when this order is itself the suspension order, its "
                     "own number"),
    DataElement(27, "suspension_order_date", "Suspension",
                "Suspension Order Date", "Suspension",
                "Date of suspension order", applies_to=(INVOKE,),
                kind="date",
                hint="Date of that order; when this order is itself the "
                     "suspension order, its own date"),
    DataElement(28, "suspension_effective_from", "Suspension",
                "Suspension Effective From", "Suspension",
                "Start date of suspension", applies_to=(INVOKE,),
                kind="date", immediate=True,
                hint="Date from which the suspension takes effect, or "
                     "'With immediate effect' if it says ತಕ್ಷಣದಿಂದ / "
                     "immediately"),
    DataElement(29, "reason_for_suspension", "Suspension",
                "Reason for Suspension", "Suspension",
                "Reason for placing employee under suspension",
                applies_to=(INVOKE,),
                hint="The reason the order gives for the suspension: "
                     "the one sentence or phrase that states what the "
                     "employee did, not the whole account of the incident"),
    DataElement(30, "lien_post", "Suspension", "Lien Post", "Suspension",
                "Substantive / lien post during suspension",
                applies_to=(INVOKE,),
                hint="Post / office where the employee's lien is kept "
                     "during suspension (ಲೀನ್ / ಲಿಯನ್), only if stated"),
    DataElement(31, "subsistence_allowance_applicable", "Suspension",
                "Subsistence Allowance Applicable", "Suspension",
                "Yes / No", applies_to=(INVOKE,), kind="yes_no",
                hint="'Yes' if the order grants subsistence allowance "
                     "(ಜೀವನಾಧಾರ ಭತ್ಯೆ), 'No' if it says none is payable, "
                     "null if it does not mention it"),
    DataElement(32, "charge_given_during_suspension", "Suspension",
                "Charge Given During Suspension", "Suspension",
                "Whether charge/charge sheet was issued during suspension "
                "— Yes/No",
                applies_to=(INVOKE,), kind="yes_no",
                hint="'Yes' if the order says a charge sheet / charge memo "
                     "(ದೋಷಾರೋಪಣಾ ಪಟ್ಟಿ) has been issued, 'No' if it says "
                     "none has, null if it does not mention it. A list of "
                     "allegations (ಆರೋಪಗಳು) in this order is not a charge "
                     "sheet"),
    DataElement(33, "charge_sheet_no", "Suspension",
                "Charge Sheet / Charge Memo No.", "Suspension",
                "Reference number of charge sheet issued during suspension",
                applies_to=(INVOKE,),
                hint="Number of that charge sheet / charge memo, only if "
                     "printed"),
)


# ---------- Suspension Extend Order ----------

EXTEND_ELEMENTS = (
    DataElement(26, "extension_required", "Suspension",
                "Extension Required", "Suspension", "Yes / No",
                applies_to=(EXTEND,), kind="yes_no",
                hint="'Yes' if the order continues / extends the "
                     "suspension (ಮುಂದುವರೆಸಿ / ವಿಸ್ತರಿಸಿ), 'No' if it "
                     "says the suspension is not extended, null if it "
                     "says neither"),
    DataElement(27, "extension_from", "Suspension", "Extension From",
                "Suspension", "Start date of extension",
                applies_to=(EXTEND,), kind="date",
                hint="Date from which the suspension is continued"),
    DataElement(28, "extension_to", "Suspension", "Extension To",
                "Suspension", "End date of extension",
                applies_to=(EXTEND,), kind="date",
                hint="Date until which the suspension is continued, only "
                     "if a date is printed (not 'until further orders')"),
    DataElement(29, "subsistence_allowance", "Suspension",
                "Subsistance Allowance", "Suspension",
                "Subsistance Allowance percentage",
                applies_to=(EXTEND,),
                hint="Percentage of subsistence allowance (ಜೀವನಾಧಾರ "
                     "ಭತ್ಯೆ) the order grants during the extension, as "
                     "written (e.g. ಶೇ 50 / 50%). Its English version is "
                     "the percentage, e.g. '50%'"),
)

# ---------- Suspension Revoke Order ----------

REVOKE_ELEMENTS = (
    DataElement(26, "suspension_effective_to", "Suspension",
                "Suspension Effective To", "Suspension",
                "End date, if specified", applies_to=(REVOKE,),
                kind="date", immediate=True,
                hint="Date on which the suspension ends (is revoked), or "
                     "'With immediate effect' if it says ತಕ್ಷಣದಿಂದ / "
                     "immediately; null if not specified"),
    DataElement(27, "reinstatement_order_no", "Suspension",
                "Reinstatement Order No.", "Suspension",
                "Reference of reinstatement order", applies_to=(REVOKE,),
                hint="Number of the order that reinstates the employee; "
                     "when this order itself reinstates, its own number"),
    DataElement(28, "reinstatement_date", "Suspension",
                "Reinstatement Date", "Suspension",
                "Date of reinstatement", applies_to=(REVOKE,),
                kind="date", immediate=True,
                hint="Date from which the employee is reinstated "
                     "(ಪುನರ್‌ಸ್ಥಾಪಿಸಿ), or 'With immediate effect'"),
    DataElement(29, "reinstatement_posting_office", "Suspension",
                "Reinstatement Posting Office", "Suspension",
                "Office Name Posting after reinstatement",
                applies_to=(REVOKE,),
                hint="Name of the office / post where the employee is "
                     "posted on reinstatement (ಸ್ಥಳ ನಿಯುಕ್ತಿ)"),
    DataElement(30, "reinstatement_posting_office_address", "Suspension",
                "Reinstatement Posting Office Address", "Suspension",
                "Office Address Posting after reinstatement",
                applies_to=(REVOKE,),
                hint="Address of that office, only if an address (place, "
                     "taluk, district) is printed"),
    DataElement(31, "reinstatement_reason", "Suspension",
                "Reinstatement Reason", "Suspension",
                "Reason for reinstatement", applies_to=(REVOKE,),
                hint="Why the order revokes the suspension (e.g. the "
                     "employee's appeal or undertaking, the result of the "
                     "enquiry, a court order), one continuous phrase from "
                     "the text. Not the action itself (revoking / "
                     "reinstating)"),
)

ELEMENTS += EXTEND_ELEMENTS + REVOKE_ELEMENTS


def elements_for(order_type: str) -> list[DataElement]:
    """Elements to extract for an order type (ALL + its own)."""
    return [e for e in ELEMENTS
            if ALL in e.applies_to or order_type in e.applies_to]


# ==========================================================================
# INVOKE / EXTEND / REVOKE ORDERS (was go_extractor/order_elements.py)
# HRMS2 data elements (data_elements.py) of a suspension order, read by
# the LLM from the whole order.
#
# As in suspension.py, every value copied from the order must be found in
# the OCR text and every date must match a date in the text; anything else
# is left empty. Copied values are kept as written (usually Kannada) in
# "original", with the model's English version as the value.
#
# Employee elements (data_elements.EMPLOYEE_KEYS) are asked for once per
# employee the order names, in an "employees" list.
# ==========================================================================

# Kinds whose values are copied from the text (and given in English too).
_ELEMENT_COPIED = ("text", "list")

_YES = re.compile(r"^(yes|y|true|ಹೌದು)\b", re.I)
_NO = re.compile(r"^(no|n|false|ಇಲ್ಲ)\b", re.I)


def extract_elements(
    text: str,
    client: LLMClient,
    order_type: str,
    pages: list[str] | None = None,
) -> DisciplinaryOrder:
    elements = elements_for(order_type)
    order_elements = [e for e in elements if not e.per_employee]
    employee_elements = [e for e in elements if e.per_employee]

    result = DisciplinaryOrder(
        order_type=order_type,
        values={e.key: None for e in order_elements},
    )

    # Two calls: Kannada values with their English versions for 30-odd
    # fields and several employees do not fit in one reply (4096 tokens).
    marked = _with_page_marks(text, pages)
    answers = _ask(client, _elements_prompt(marked, order_type, order_elements, []))
    employee_answers = _ask(
        client, _elements_prompt(marked, order_type, [], employee_elements)
    )

    dates = _dates_in(text)

    _fill(result.values, result.original, order_elements, answers, text, dates)
    _check_order(result, text)
    result.sources = {key: "llm" for key, value in result.values.items()
                      if value}

    employees = employee_answers.get("employees")

    if isinstance(employees, dict):
        employees = [employees]
    if not isinstance(employees, list):
        employees = []

    for answer in employees:
        if not isinstance(answer, dict):
            continue

        employee = EmployeeElements(
            values={e.key: None for e in employee_elements}
        )
        _fill(employee.values, employee.original, employee_elements,
              answer, text, dates)
        _check_employee(employee, text)

        # Nothing of this employee was found in the text.
        if any(employee.values.values()):
            result.employees.append(employee)

    return result


def _ask(client: LLMClient, prompt: str) -> dict:
    reply = client.generate(prompt)

    # "{}": the model found none of the fields (all are left out as null).
    if re.fullmatch(r"\s*\{\s*\}\s*", reply):
        logger.info("LLM found none of the fields")
        return {}

    answers = _parse(reply) or _complete_part(reply)

    if not answers:
        raise ValueError(f"LLM reply was not JSON: {reply[:200]!r}")

    return answers


def _complete_part(reply: str) -> dict:
    """
    A reply cut off at the token limit: the fields before the cut, closed
    as JSON. The fields after it stay blank (never filled by guessing).
    """
    cuts = [m.start() + 1 for m in re.finditer(r'"\s*,\s*"', reply)]

    for cut in reversed(cuts[-60:]):
        for ending in ("}", "}]}", "]}"):
            answers = _parse(reply[:cut] + ending)

            if answers:
                logger.warning("LLM reply was cut off; kept the %d keys "
                               "before the cut", len(answers))
                return answers

    return {}


# A rule book, not an authority: "... ನಿಯಮಗಳು 1965 ರ ನಿಯಮ 5(1)".
_RULE_WORDS = re.compile(r"ನಿಯಮ|\brules?\b", re.I)


# This order's own number is the suspension / reinstatement order number
# only when its subject line says it suspends / revokes (the file name's
# type is not proof: a memo filed as an invoke order is not one).
_OWN_NUMBER = {
    "suspension_order_no": "Suspension",
    "reinstatement_order_no": "Suspension revoked",
}


def _check_order(result: DisciplinaryOrder, text: str) -> None:
    """Values that are clearly in the wrong field."""
    values, original = result.values, result.original
    subject_type = _subject_type(text)

    for key, does in _OWN_NUMBER.items():
        own = original.get(key)

        if (own and own == original.get("order_no")
                and subject_type and subject_type != does):
            logger.info("%s is this order's own number, but its subject "
                        "reads as %s", key, subject_type)
            values[key] = None
            del original[key]

    for key in ("competent_authority", "disciplinary_authority_name"):
        if values.get(key) and _RULE_WORDS.search(original.get(key, "")):
            logger.info("%s is a rule: %r", key, original[key])
            values[key] = None
            original.pop(key, None)

    # The case is not this order: its own number is not the case number.
    case = original.get("disciplinary_case_no")

    if case and case == original.get("order_no"):
        logger.info("Case number is the order number: %r", case)
        values["disciplinary_case_no"] = None
        del original["disciplinary_case_no"]


def _fill(values, originals, elements, answers: dict, text: str, dates):
    """Checked values of the elements from one JSON object of the reply."""
    for element in elements:
        answer = answers.get(element.key)
        english = answers.get(f"{element.key}_en")

        if element.kind == "list":
            original, value = _list(answer, english, text)
        elif element.kind == "text":
            original = _copied(answer, text, element.key)
            value = original and _english(english, original)
        else:
            original, value = None, _checked(element, answer, dates)

        if not value:
            continue

        if element.evidence and not re.search(element.evidence, text, re.I):
            logger.info("%s not mentioned in the text: %r", element.key, value)
            continue

        if element.label_words and not _labelled(
            original or value, element.label_words, text
        ):
            logger.info("%s not labelled in the text: %r", element.key, value)
            continue

        if element.not_after and _labelled(
            original or value, element.not_after, text
        ):
            logger.info("%s is another number: %r", element.key, value)
            continue

        if element.near_words and not _date_near(
            value, element.near_words, text
        ):
            logger.info("%s not printed near %s: %r", element.key,
                        element.near_words, value)
            continue

        values[element.key] = value

        if original:
            originals[element.key] = original


# Honorifics before a name: ಶ್ರೀ / ಶ್ರೀಮತಿ / ಕು. / Sri / Shri / Smt ...
_HONORIFIC = re.compile(
    r"^(?:ಶ್ರೀಮತಿ|ಶ್ರೀ|ಕುಮಾರಿ|ಕು\.|shrimati|smt\.?|shri|sri|kumari|ku\.)\s*",
    re.I,
)

# How far after the employee's name their designation may be printed.
DESIGNATION_WINDOW = 40


def _labelled(value: str, label: str, text: str) -> bool:
    """The value is printed shortly after its label (e.g. KGID: 1234567)."""
    pattern = rf"(?:{label})[^\n]{{0,25}}?{re.escape(value.strip())}"
    return bool(re.search(pattern, text, re.I))


def _date_near(value: str, words: str, text: str) -> bool:
    """The date (DD.MM.YYYY) is printed with these words shortly after."""
    if value == IMMEDIATE:
        return True

    prepared = prepare(text)
    wanted = _date_key(*value.split("."))

    for match in _DATE.finditer(prepared):
        if _date_key(*match.groups()) == wanted and re.search(
            words, prepared[match.end():match.end() + NEAR_WINDOW], re.I
        ):
            return True

    return False


def _check_employee(employee: EmployeeElements, text: str) -> None:
    """Name without honorific; designation must follow that name."""
    values, original = employee.values, employee.original

    for store in (values, original):
        if store.get("employee_name"):
            store["employee_name"] = _HONORIFIC.sub("", store["employee_name"])

    name = original.get("employee_name")
    designation = original.get("designation")

    if not name or not designation:
        return

    flat = re.sub(r"\s+", " ", text)
    name_at = [m.end() for m in re.finditer(re.escape(name), flat)]

    # Name not printed exactly as copied (OCR spelling): cannot check.
    if not name_at:
        return

    if not any(designation in flat[end:end + DESIGNATION_WINDOW + len(designation)]
               for end in name_at):
        logger.info("Designation %r not next to %r", designation, name)
        values["designation"] = None
        del original["designation"]


# ==========================================================
# PROMPT
# ==========================================================


def _help(element: DataElement) -> str:
    help = f"{element.label} — {element.description}."

    if element.hint:
        help += f" {element.hint}."

    if element.kind == "date":
        help += " DD.MM.YYYY."
    elif element.kind == "list":
        help += " A JSON list of strings."
    elif element.kind == "yes_no":
        help += " 'Yes' or 'No'."

    return help


def _fields(elements: list[DataElement]) -> dict[str, str]:
    """JSON key -> what goes in it, with the "..._en" keys."""
    fields = {}

    for element in elements:
        fields[element.key] = _help(element)

        if element.kind in _ELEMENT_COPIED:
            fields[f"{element.key}_en"] = (
                "English version of each item of the list before it, in "
                "the same order. " + ENGLISH_HELP
                if element.kind == "list" else ENGLISH_HELP
            )

    return fields


def _elements_prompt(
    text: str,
    order_type: str,
    order_elements: list[DataElement],
    employee_elements: list[DataElement],
) -> str:
    order_fields = _fields(order_elements)
    employee_fields = _fields(employee_elements)

    def lines(fields: dict[str, str], indent: str) -> str:
        return "\n".join(f'{indent}- "{name}": {help}'
                         for name, help in fields.items())

    # One call asks for the order's fields, another for the employees'.
    if employee_fields:
        example = json.dumps(
            {"employees": [{name: "..." for name in employee_fields}]}
        )
        wanted = (
            '- "employees": a list with one object for each employee this '
            "order is about, in the order they are named. Each object has "
            "these fields of that employee:\n"
            + lines(employee_fields, "    ")
        )
    else:
        example = json.dumps({name: "..." for name in order_fields})
        wanted = lines(order_fields, "")

    return f"""The text below is an OCR scan of a Karnataka government order (Kannada and/or English): a {order_type}, which {ORDER_DESCRIPTIONS[order_type]}. OCR may have small errors.

Extract these fields:
{wanted}

Rules:
- Fill every field the document states, wherever in the document it is stated. Use null only when the document does not state it or it cannot be reliably identified. Never infer, guess or fill a value from general knowledge.
- The main order is the order that starts on page 1: take its number, date, subject and signing authority from it, not from the references or from other documents on later pages (earlier orders, memos, duty reports). Other fields may come from any page that states them for this employee and this action.
- List every employee the order is about, each once. Give each employee only the details the text states for that employee; never mix details of different employees, and do not copy one employee's detail to another unless the text states it for both.
- A value must be written in the text, not worked out: do not compute dates and do not assume a value because orders like this usually have one. The same value may fill several fields when the document supports each (e.g. an order that suspends the employee is itself the suspension order, so its number is also the Suspension Order No.).
- A number is an identifier (KGID, DDO code) only if the text says what it is; a bare number written next to a name is not a KGID.
- Copy values exactly as written in the text, in Kannada script when the text is in Kannada. Never translate or transliterate them into Hindi / Devanagari. Each "..._en" key holds the English version of the key before it; give it only when the value is not already in English.
- Copy one continuous phrase from the text for each value (or list item); do not join several phrases. Copy only the words that answer the field (at most about 40 words), never a whole paragraph.
- Write dates as DD.MM.YYYY.
- Leave out every key whose value would be null (and its "..._en" key), so the reply stays short.

Text:
{text}

Return one JSON object with these keys, leaving out the null ones: {example}"""


# ==========================================================
# VALIDATION
# ==========================================================


def _clean(value) -> str | None:
    if not isinstance(value, str):
        return None

    value = re.sub(r"\s+", " ", value).strip(" ,;:")
    return value if value and value.lower() not in ("null", "none") else None


def _copied(value, text: str, key: str) -> str | None:
    """The value as written, if it is found in the text."""
    value = _clean(value)

    if not value:
        return None

    if "�" in value:
        value = _repair(value, text)

    if not _appears_in(value, text):
        logger.info("LLM value for %s not found in text: %r", key, value)
        return None

    return value


def _list(answer, english, text: str) -> tuple[str | None, str | None]:
    """Items found in the text, one per line: (as written, English)."""
    if isinstance(answer, str):
        answer = [answer]
    if not isinstance(answer, list):
        return None, None
    if not isinstance(english, list) or len(english) != len(answer):
        english = [None] * len(answer)

    originals, values = [], []

    for item, item_en in zip(answer, english):
        original = _copied(item, text, "list item")

        if original:
            originals.append(original)
            values.append(_english(item_en, original))

    if not originals:
        return None, None

    return "\n".join(originals), "\n".join(values)


def _checked(element: DataElement, value, dates) -> str | None:
    """Dates, Yes/No and labels (not copied from the text)."""
    value = _clean(value)

    if not value:
        return None

    if element.kind == "date":
        date = _date(value, dates)

        if date == IMMEDIATE and not element.immediate:
            return None

        return date

    if element.kind == "yes_no":
        if _YES.match(value):
            return "Yes"
        if _NO.match(value):
            return "No"
        return None

    # label: an English phrase chosen by the model.
    if _KANNADA.search(value) or len(value) > 60:
        logger.info("LLM %s not usable: %r", element.key, value)
        return None

    return value


# ==========================================================================
# PIPELINE (was go_extractor/pipeline.py)
# GO extraction pipeline: file -> text (OCR) -> document type -> fields ->
# DocumentResult. Each step is logged to the terminal with timing.
#
#     advocate GOs     rules, then the LLM fills gaps
#     suspension orders  LLM reads the whole order (suspension.py);
#                        invoke orders: HRMS2 data elements (order_elements.py)
# ==========================================================================

class GOPipeline:

    def __init__(self) -> None:
        self.llm = LLMClient()

    def process(self, path: str | Path, filename: str | None = None) -> DocumentResult:
        path = Path(path)
        name = filename or path.name
        timings: dict[str, float] = {}

        def log(message: str) -> None:
            print(f"{datetime.now():%H:%M:%S} [GO] {name} | {message}",
                  file=sys.stderr, flush=True)

        start = time.perf_counter()

        if self.llm is not None:
            self.llm.calls, self.llm.seconds = 0, 0.0

        try:
            log("OCR ...")
            ocr = extract_text(path)
            timings["OCR"] = ocr.seconds
            log(f"OCR done in {ocr.seconds:.1f}s ({ocr.method}, "
                f"{len(ocr.pages)} page(s), {len(ocr.text)} chars)")

            if not ocr.text.strip():
                raise ValueError("No text could be read from the document.")

            # A file named as an invoke / extend / revoke order is one, even
            # if its text (e.g. an unreadable page) does not say so.
            named_type = type_from_filename(name)

            if named_type or is_suspension_order(ocr.text):
                return self._suspension(name, ocr, timings, start, log,
                                        named_type)

            step = time.perf_counter()
            fields = extract_advocate(ocr.text)
            timings["Rules"] = time.perf_counter() - step
            missing = fields.missing_fields()
            log(f"Rules done in {timings['Rules']:.2f}s, "
                f"missing: {', '.join(missing) or 'none'}")

            if missing and self.llm is not None:
                step = time.perf_counter()
                log(f"LLM ({self.llm.name}) for {len(missing)} field(s) ...")

                try:
                    fields = fill_missing(fields, ocr.text, self.llm)
                except Exception as e:
                    log(f"LLM failed, keeping rule results: {e}")

                timings["LLM"] = time.perf_counter() - step
                log(f"LLM done in {timings['LLM']:.1f}s, still missing: "
                    f"{', '.join(fields.missing_fields()) or 'none'}")

            self._total(timings, start, log)

            # Nothing at all found: not a "Success" with an empty row.
            error = None

            if len(fields.missing_fields()) == len(GOExtraction().missing_fields()):
                error = ("Not recognised as a Government Advocate order or a "
                         "suspension order: nothing could be extracted. "
                         "Check the extracted text.")
                log(error)

            return DocumentResult(
                filename=name,
                fields=fields,
                text=ocr.text,
                ocr_method=ocr.method,
                timings=timings,
                error=error,
                pages=len(ocr.pages),
                warnings=ocr.warnings,
            )

        except Exception as e:
            logger.exception("Failed to process %s", name)
            log(f"FAILED: {e}")

            return DocumentResult(
                filename=name,
                fields=GOExtraction(),
                text="",
                ocr_method="",
                timings=timings,
                error=str(e),
            )

    def _suspension(self, name, ocr, timings, start, log,
                    named_type=None) -> DocumentResult:
        if self.llm is None:
            raise ValueError(
                "Suspension orders need the LLM: set LLM_PROVIDER in .env."
            )

        # The order type decides the fields: from the file name, else from
        # the subject line, else from the LLM's reading of the whole order.
        step = time.perf_counter()
        fields = None
        subject_type = _subject_type(ocr.text)
        order_type = subject_type

        if named_type:
            order_type = next(t for t, d in ORDER_TYPES.items()
                              if d == named_type)
        elif order_type is None:
            log(f"Suspension order, LLM ({self.llm.name}) ...")
            fields = extract_suspension(ocr.text, self.llm, ocr.pages)
            order_type = fields.order_type

        doc_type = ORDER_TYPES.get(order_type)

        if doc_type:
            log(f"{doc_type}, data elements, LLM ({self.llm.name}) ...")
            fields = extract_elements(
                ocr.text, self.llm, doc_type, ocr.pages
            )

            # Only warnings: the file name's type is kept.
            if named_type and not is_suspension_order(ocr.text):
                fields.type_check = (
                    f"Named {doc_type}, but the text does not mention "
                    "suspension (another kind of document?)"
                )
                log(fields.type_check)
            elif subject_type and subject_type != order_type:
                subject_doc = ORDER_TYPES.get(
                    subject_type, subject_type
                )
                fields.type_check = (
                    f"Named {doc_type}, but its subject line reads as "
                    f"{subject_doc}"
                )
                log(fields.type_check)
        else:
            doc_type = "suspension"

            if fields is None:
                log(f"Suspension order, LLM ({self.llm.name}) ...")
                fields = extract_suspension(ocr.text, self.llm, ocr.pages)

        timings["LLM"] = time.perf_counter() - step
        log(f"LLM done in {timings['LLM']:.1f}s, missing: "
            f"{', '.join(fields.missing_fields()) or 'none'}")

        self._total(timings, start, log)

        return DocumentResult(
            filename=name,
            fields=fields,
            text=ocr.text,
            ocr_method=ocr.method,
            timings=timings,
            doc_type=doc_type,
            pages=len(ocr.pages),
            warnings=ocr.warnings,
        )

    def _total(self, timings: dict, start: float, log) -> None:
        """Total time, and the LLM calls this document took."""
        timings["Total"] = time.perf_counter() - start
        calls = self.llm.calls if self.llm is not None else 0

        if calls:
            timings["LLM calls"] = calls

        log(f"Total {timings['Total']:.1f}s (OCR {timings.get('OCR', 0):.1f}s, "
            f"LLM {timings.get('LLM', 0):.1f}s in {calls} call(s))")


# ==========================================================================
# COMMAND LINE
# ==========================================================================

def collect(paths: list[str]) -> list[Path]:
    """The PDF / image files: files as given, folders searched recursively."""
    files: list[Path] = []

    for raw in paths:
        path = Path(raw)

        if path.is_dir():
            files += sorted(
                p for p in path.rglob("*")
                if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
            )
        elif path.is_file():
            files.append(path)
        else:
            print(f"Not found: {path}", file=sys.stderr)

    return files


def pick_folder() -> str:
    """Choose the folder in a folder picker (tkinter, part of Python on Windows)."""
    try:
        from tkinter import Tk, filedialog
    except ImportError:
        sys.exit("No folder given, and there is no folder picker (tkinter) here. "
                 "Give the folder: python ccms_pipeline.py <folder>")

    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(parent=root, title="Folder with PDF / image files",
                                     mustexist=True)
    root.destroy()

    if not folder:
        sys.exit("No folder chosen.")

    return folder


def response(result: DocumentResult, include_text: bool = False) -> dict:
    """What the script answers for one document."""
    answer = {
        "document": result.filename,
        "type": result.doc_type,
        "status": "Failed" if result.error else "Success",
        "error": result.error,
        "pages": result.pages,
        "warnings": result.warnings,
        "fields": result.fields.to_dict(),
        "timings": {key: round(value, 1) for key, value in result.timings.items()},
    }

    if include_text:
        answer["text"] = result.text

    return answer


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # Kannada on the Windows console
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*",
                        help="PDF / image files or folders (none: choose a folder)")
    parser.add_argument("--out", metavar="FILE",
                        help="also save the response (JSON) to this file")
    parser.add_argument("--workers", type=int, default=1, metavar="N",
                        help="documents processed at the same time (default 1)")
    parser.add_argument("--text", action="store_true",
                        help="include the OCR text in the response")
    parser.add_argument("--verbose", action="store_true",
                        help="also show each OCR / LLM call and each value a check dropped")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        stream=sys.stderr, format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    files = collect(args.paths or [pick_folder()])

    if not files:
        print("No PDF or image files to process.", file=sys.stderr)
        return 2

    # Both servers first: a run with one of them down would only fail.
    down = False
    for name, url in (("OCR server", OCR_URL), ("LLM server", LLM_URL)):
        up, seen = check_server(name, url)
        print(seen, file=sys.stderr, flush=True)
        down = down or not up

    if down:
        print("Not started: a server is down. Try again when it answers.",
              file=sys.stderr)
        return 2

    workers = max(1, min(args.workers, len(files)))
    print(f"{len(files)} file(s), {workers} at a time", file=sys.stderr, flush=True)

    # One pipeline per thread: it keeps per-document LLM counters.
    local = threading.local()

    def process(path: Path) -> DocumentResult:
        if not hasattr(local, "pipeline"):
            local.pipeline = GOPipeline()
        return local.pipeline.process(path)

    results: dict[Path, DocumentResult] = {}
    start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process, path): path for path in files}

        for number, future in enumerate(as_completed(futures), start=1):
            path = futures[future]
            result = future.result()
            results[path] = result
            status = f"FAILED: {result.error}" if result.error else "done"
            print(f"[{number}/{len(files)}] {path.name}: {status}",
                  file=sys.stderr, flush=True)

    # The response, in the files' order.
    answers = [response(results[path], args.text) for path in files]
    output = json.dumps(answers[0] if len(answers) == 1 else answers,
                        ensure_ascii=False, indent=2)
    print(output)

    if args.out:
        Path(args.out).write_text(output + "\n", encoding="utf-8")
        print(f"Saved to {args.out}", file=sys.stderr)

    failed = [a for a in answers if a["error"]]
    print(f"{len(answers) - len(failed)} of {len(answers)} document(s) done in "
          f"{(time.perf_counter() - start) / 60:.1f} min"
          + (f", {len(failed)} failed" if failed else ""), file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
