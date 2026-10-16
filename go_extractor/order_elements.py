"""
HRMS2 data elements (data_elements.py) of a suspension order, read by
the LLM from the whole order.

As in suspension.py, every value copied from the order must be found in
the OCR text and every date must match a date in the text; anything else
is left empty. Copied values are kept as written (usually Kannada) in
"original", with the model's English version as the value.

Employee elements (data_elements.EMPLOYEE_KEYS) are asked for once per
employee the order names, in an "employees" list.
"""

from __future__ import annotations

import json
import logging
import re

from go_extractor import rules, suspension
from go_extractor.data_elements import (
    NEAR_WINDOW, ORDER_DESCRIPTIONS, DataElement, elements_for,
)
from go_extractor.llm.clients import LLMClient
from go_extractor.llm.fallback import _appears_in, _parse
from go_extractor.models import DisciplinaryOrder, EmployeeElements

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
    order_elements = [e for e in elements if not e.per_employee]
    employee_elements = [e for e in elements if e.per_employee]

    result = DisciplinaryOrder(
        order_type=order_type,
        values={e.key: None for e in order_elements},
    )

    # Two calls: Kannada values with their English versions for 30-odd
    # fields and several employees do not fit in one reply (4096 tokens).
    marked = suspension._with_page_marks(text, pages)
    answers = _ask(client, _prompt(marked, order_type, order_elements, []))
    employee_answers = _ask(
        client, _prompt(marked, order_type, [], employee_elements)
    )

    dates = suspension._dates_in(text)

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
    subject_type = suspension._subject_type(text)

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
            value = original and suspension._english(english, original)
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
    if value == suspension.IMMEDIATE:
        return True

    prepared = rules.prepare(text)
    wanted = suspension._date_key(*value.split("."))

    for match in suspension._DATE.finditer(prepared):
        if suspension._date_key(*match.groups()) == wanted and re.search(
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

        if element.kind in _COPIED:
            fields[f"{element.key}_en"] = (
                "English version of each item of the list before it, in "
                "the same order. " + suspension.ENGLISH_HELP
                if element.kind == "list" else suspension.ENGLISH_HELP
            )

    return fields


def _prompt(
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

        if date == suspension.IMMEDIATE and not element.immediate:
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
