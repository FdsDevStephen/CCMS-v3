"""
Suspension orders (Kannada / English): orders that suspend a government
servant, extend a suspension, or revoke it and reinstate them.

Their wording differs by department (police, panchayat, forest, transport,
education, land records ...), so the LLM reads the whole order and every
value it copies is checked against the OCR text, as in llm/fallback.py.
Dates are normalised to DD.MM.YYYY and must match a date in the text.
"""

from __future__ import annotations

import json
import logging
import re

from go_extractor.llm.clients import LLMClient
from go_extractor.llm.fallback import _appears_in, _parse
from go_extractor.models import SuspensionOrder
from go_extractor.rules import prepare

logger = logging.getLogger(__name__)

# Suspension orders run to 4 pages; the model has a 32k context.
MAX_TEXT_CHARS = 16000

ORDER_TYPES = (
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

FIELD_HELP = {
    "order_type": (
        f"One of {', '.join(repr(t) for t in ORDER_TYPES)}, for the main "
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
_COPIED = (
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


def extract(
    text: str,
    client: LLMClient,
    pages: list[str] | None = None,
) -> SuspensionOrder:
    result = SuspensionOrder()
    reply = client.generate(_prompt(_with_page_marks(text, pages)))
    answers = _parse(reply)

    if not answers:
        raise ValueError(f"LLM reply was not JSON: {reply[:200]!r}")

    dates = _dates_in(text)

    for name in FIELD_HELP:
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
        elif name in _COPIED and not _appears_in(value, text):
            logger.info("LLM value for %s not found in text: %r", name, value)
            value = None

        if not value:
            continue

        if name in _COPIED:
            result.original[name] = original
            value = _english(answers.get(f"{name}_en"), original)

        if name == "issuing_authority":
            result.original[name] = _SERVICE.sub("", original)
            value = _SERVICE.sub("", value)

        _set(result, name, value)
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
        return text[:MAX_TEXT_CHARS]

    marked = "\n\n".join(
        f"=== Page {number} ===\n{page}"
        for number, page in enumerate(pages, start=1) if page.strip()
    )
    return marked[:MAX_TEXT_CHARS]


def _prompt(text: str) -> str:
    fields = {}

    for name, help in FIELD_HELP.items():
        fields[name] = help

        if name in _COPIED:
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
    for order_type in ORDER_TYPES:
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


def _set(result: SuspensionOrder, name: str, value: str) -> None:
    if name == "issuing_authority":
        result.issuing_authority.name = value
    elif name == "issuing_authority_designation":
        result.issuing_authority.designation = value
    else:
        setattr(result, name, value)

