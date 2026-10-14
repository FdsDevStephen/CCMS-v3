"""
HRMS2 data elements (data_elements.py) of a suspension order, read by
the LLM from the whole order.

As in suspension.py, every value copied from the order must be found in
the OCR text and every date must match a date in the text; anything else
is left empty. Copied values are kept as written (usually Kannada) in
"original", with the model's English version as the value.
"""

from __future__ import annotations

import json
import logging
import re

from go_extractor import suspension
from go_extractor.data_elements import DataElement, elements_for
from go_extractor.llm.clients import LLMClient
from go_extractor.llm.fallback import _appears_in, _parse
from go_extractor.models import DisciplinaryOrder

logger = logging.getLogger(__name__)

# Kinds whose values are copied from the text (and given in English too).
_COPIED = ("text", "list")

_YES = re.compile(r"^(yes|y|true|ಹೌದು)\b", re.I)
_NO = re.compile(r"^(no|n|false|ಇಲ್ಲ)\b", re.I)


def extract(
    text: str,
    client: LLMClient,
    order_type: str,
    pages: list[str] | None = None,
) -> DisciplinaryOrder:
    elements = elements_for(order_type)
    result = DisciplinaryOrder(
        order_type=order_type,
        values={e.key: None for e in elements},
    )

    marked = suspension._with_page_marks(text, pages)
    reply = client.generate(_prompt(marked, order_type, elements))
    answers = _parse(reply)

    if not answers:
        raise ValueError(f"LLM reply was not JSON: {reply[:200]!r}")

    dates = suspension._dates_in(text)

    for element in elements:
        answer = answers.get(element.key)
        english = answers.get(f"{element.key}_en")

        if element.kind == "list":
            original, value = _list(answer, english, text)
        elif element.kind == "text":
            original = _copied(answer, text, element.key)
            value = original and suspension._english(english, original)
        else:
            original, value = None, _checked(element, answer, dates)

        if not value:
            continue

        result.values[element.key] = value
        result.sources[element.key] = "llm"

        if original:
            result.original[element.key] = original

    return result


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


def _prompt(text: str, order_type: str, elements: list[DataElement]) -> str:
    fields = {}

    for element in elements:
        fields[element.key] = _help(element)

        if element.kind in _COPIED:
            fields[f"{element.key}_en"] = (
                "English version of each item of the list before it, in "
                "the same order. " + suspension.ENGLISH_HELP
                if element.kind == "list" else suspension.ENGLISH_HELP
            )

    keys = "\n".join(f'- "{name}": {help}' for name, help in fields.items())
    example = json.dumps({name: "..." for name in fields})

    return f"""The text below is an OCR scan of a Karnataka government order (Kannada and/or English): a {order_type}, which places a government employee under suspension. OCR may have small errors.

Extract these fields:
{keys}

Rules:
- Only extract what the document states. If a field is not in the text or cannot be reliably identified, use null. Never infer, guess or fill a value from general knowledge.
- The main order is the newest order this document issues; it starts on page 1. Later pages may be copies of earlier orders it refers to: do not take this order's number or date from those.
- Copy values exactly as written in the text, in Kannada script when the text is in Kannada. Never translate or transliterate them into Hindi / Devanagari. Each "..._en" key holds the English version of the key before it.
- Copy one continuous phrase from the text for each value (or list item); do not join several phrases.
- Write dates as DD.MM.YYYY.

Text:
{text}

Return one JSON object with exactly these keys: {example}"""


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
        value = suspension._repair(value, text)

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
            values.append(suspension._english(item_en, original))

    if not originals:
        return None, None

    return "\n".join(originals), "\n".join(values)


def _checked(element: DataElement, value, dates) -> str | None:
    """Dates, Yes/No and labels (not copied from the text)."""
    value = _clean(value)

    if not value:
        return None

    if element.kind == "date":
        date = suspension._date(value, dates)

        if date == suspension.IMMEDIATE and "effective" not in element.key:
            return None

        return date

    if element.kind == "yes_no":
        if _YES.match(value):
            return "Yes"
        if _NO.match(value):
            return "No"
        return None

    # label: an English phrase chosen by the model.
    if suspension._KANNADA.search(value) or len(value) > 60:
        logger.info("LLM %s not usable: %r", element.key, value)
        return None

    return value
