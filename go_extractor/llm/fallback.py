"""
LLM fallback: fill only the fields the rules left empty.

Every value the model returns is checked against the document text and
dropped if it does not appear there (fuzzy match, to tolerate small OCR
differences). This keeps hallucinated names, numbers and dates out.
"""

from __future__ import annotations

import json
import logging
import re

from rapidfuzz import fuzz

from go_extractor.llm.clients import LLMClient
from go_extractor.models import GOExtraction

logger = logging.getLogger(__name__)

# Minimum fuzzy similarity (0-100) between a value and the document text.
MIN_MATCH = 88

# Max characters of document text sent to the model (GOs are short).
MAX_TEXT_CHARS = 8000

FIELD_HELP = {
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

    reply = client.generate(_prompt(missing, text[:MAX_TEXT_CHARS]))
    answers = _parse(reply)

    for name in missing:
        value = answers.get(name)

        if not isinstance(value, str) or not value.strip():
            continue

        value = re.sub(r"\s+", " ", value).strip(" ,;:")

        if not _appears_in(value, text):
            logger.info("LLM value for %s not found in text: %r", name, value)
            continue

        _set(result, name, value)
        result.sources[name.replace("_designation", "")] = "llm"

    return result


def _prompt(fields: list[str], text: str) -> str:
    keys = "\n".join(f'- "{name}": {FIELD_HELP[name]}' for name in fields)
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

    return fuzz.partial_ratio(value, text) >= MIN_MATCH


def _set(result: GOExtraction, name: str, value: str) -> None:
    if name == "litigation_officer":
        result.litigation_officer.officer = value
    elif name == "issuing_authority":
        result.issuing_authority.name = value
    elif name == "issuing_authority_designation":
        result.issuing_authority.designation = value
    else:
        setattr(result, name, value)
