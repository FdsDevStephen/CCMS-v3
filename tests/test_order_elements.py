"""
HRMS2 data elements of Suspension Invoke Orders, with a fake LLM so the
tests run offline.

    python -m pytest tests
"""

import json

from go_extractor import order_elements
from go_extractor.data_elements import (
    ELEMENTS, INVOKE, ORDER_TYPES, elements_for,
)
from go_extractor.exporter import SHEETS, to_row
from go_extractor.models import DocumentResult

from tests.test_suspension import TEXT


class FakeClient:
    name = "fake"

    def __init__(self, answers: dict):
        self.reply = json.dumps(answers, ensure_ascii=False)
        self.prompt = ""

    def generate(self, prompt: str) -> str:
        self.prompt = prompt
        return self.reply


def test_invoke_order_has_all_33_elements():
    elements = elements_for(INVOKE)

    assert [e.number for e in elements] == list(range(1, 34))
    assert len({e.key for e in ELEMENTS}) == len(ELEMENTS)
    assert ORDER_TYPES["Suspension"] == INVOKE


def test_prompt_asks_for_every_element_and_no_guessing():
    client = FakeClient({"employee_name": None})
    result = order_elements.extract(TEXT, client, INVOKE)

    for element in elements_for(INVOKE):
        assert f'"{element.key}"' in client.prompt
    assert '"employee_name_en"' in client.prompt
    assert '"order_date_en"' not in client.prompt
    assert "Never infer" in client.prompt
    # Every element is present, blank when not found.
    assert set(result.values) == {e.key for e in elements_for(INVOKE)}
    assert all(value is None for value in result.values.values())


def test_values_found_in_text_are_kept():
    result = order_elements.extract(TEXT, FakeClient({
        "document_type": "Suspension Order",
        "employee_name": "ಸಿ.ವ್ಹಿ.ಇಂಗಳಹಳ್ಳಿ",
        "employee_name_en": "C.V. Ingalahalli",
        "designation": "ಎಎಸ್‌ಐ",
        "designation_en": "ASI",
        "office_name": "ಗದಗ ಶಹರ ಪೊಲೀಸ್‌ ಠಾಣೆ",
        "order_no": "ಇವಿ/ಅಮಾನತ್ತು-03/14 /2026",
        "order_date": "26-2-2026",
        "disciplinary_case_no": "ಪ್ರಕರಣ ಸಂಖ್ಯೆ:03/2026",
        "case_registration_date": "25.02.2026",
        "suspension_order_no": "ಇವಿ/ಅಮಾನತ್ತು-03/14 /2026",
        "suspension_order_date": "26.02.2026",
        "suspension_effective_from": "With immediate effect",
        "reason_for_suspension": (
            "ಟ್ರ್ಯಾಪ್‌ ಕಾರ್ಯಾಚರಣೆಯಲ್ಲಿ ಸಿಕ್ಕಿಬಿದ್ದಿರುವುದು"
        ),
        "reason_for_suspension_en": "Caught in a trap operation",
        "copy_to": ["ಪೊಲೀಸ್‌ ಅಧೀಕ್ಷಕರು, ಗದಗ ಜಿಲ್ಲೆ", "ಬೆಂಗಳೂರು ನಗರ"],
        "copy_to_en": ["Superintendent of Police, Gadag District",
                       "Bengaluru City"],
        "subsistence_allowance_applicable": "yes",
        "charge_given_during_suspension": "Not mentioned",
    }), INVOKE)

    v = result.values
    assert v["document_type"] == "Suspension Order"
    assert v["employee_name"] == "C.V. Ingalahalli"
    assert result.original["employee_name"] == "ಸಿ.ವ್ಹಿ.ಇಂಗಳಹಳ್ಳಿ"
    assert v["designation"] == "ASI"
    # No English given: the Kannada value is kept.
    assert v["office_name"] == "ಗದಗ ಶಹರ ಪೊಲೀಸ್‌ ಠಾಣೆ"
    assert v["order_date"] == "26.02.2026"
    assert v["case_registration_date"] == "25.02.2026"
    assert v["suspension_effective_from"] == "With immediate effect"
    assert v["reason_for_suspension"] == "Caught in a trap operation"
    # Only the copy-to item found in the text is kept.
    assert v["copy_to"] == "Superintendent of Police, Gadag District"
    assert v["subsistence_allowance_applicable"] == "Yes"
    assert v["charge_given_during_suspension"] is None
    assert result.missing_fields() == []


def test_values_not_in_text_are_left_blank():
    result = order_elements.extract(TEXT, FakeClient({
        "employee_name": "ರಾಮಣ್ಣ ಕೃಷ್ಣಪ್ಪ ನಾಯಕ",
        "employee_name_en": "Ramanna Krishnappa Nayak",
        "employee_kgid": "1234567",
        "order_date": "01.01.2026",
        # "Immediately" is only a value for the effective-from date.
        "approval_date": "With immediate effect",
        "document_type": "ಅಮಾನತ್ತು ಆದೇಶ",
    }), INVOKE)

    assert result.values["employee_name"] is None
    assert result.values["employee_kgid"] is None
    assert result.values["order_date"] is None
    assert result.values["approval_date"] is None
    assert result.values["document_type"] is None
    assert result.missing_fields() == [
        "employee_name", "designation", "order_no", "order_date",
    ]


def test_excel_row_has_every_element_column():
    fields = order_elements.extract(TEXT, FakeClient({
        "employee_name": "ಸಿ.ವ್ಹಿ.ಇಂಗಳಹಳ್ಳಿ",
        "employee_name_en": "C.V. Ingalahalli",
    }), INVOKE)
    result = DocumentResult("order.pdf", fields, TEXT, "api", doc_type=INVOKE)

    title, columns = SHEETS[INVOKE]
    row = to_row(result)

    assert title == "Suspension Invoke Orders"
    assert set(row) == {name for name, _ in columns}
    assert row["Employee Name"] == "C.V. Ingalahalli"
    assert row["Employee Name (Kannada)"] == "ಸಿ.ವ್ಹಿ.ಇಂಗಳಹಳ್ಳಿ"
    assert row["DDO Code"] == ""
