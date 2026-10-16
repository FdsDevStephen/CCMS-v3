"""
HRMS2 data elements of Suspension Invoke Orders, with a fake LLM so the
tests run offline.

    python -m pytest tests
"""

import json

from go_extractor import order_elements
from go_extractor.data_elements import (
    ELEMENTS, EXTEND, INVOKE, ORDER_TYPES, REVOKE, elements_for,
)
from go_extractor.exporter import SHEETS, to_rows
from go_extractor.models import DocumentResult

from tests.test_suspension import TEXT


class FakeClient:
    name = "fake"

    def __init__(self, answers: dict):
        self.reply = json.dumps(answers, ensure_ascii=False)
        self.prompt = ""

    def generate(self, prompt: str) -> str:
        # Every prompt sent (one for the order, one for the employees).
        self.prompt += prompt
        return self.reply


# Made-up excerpts in the wording of the extend / revoke samples.
EXTEND_TEXT = """ಸಂಖ್ಯೆ: ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/22/2026 ದಿನಾಂಕ: 10.03.2026
ವಿಷಯ: ಶ್ರೀ ಕೆ.ರಮೇಶ್, ಪ್ರಥಮ ದರ್ಜೆ ಸಹಾಯಕರು ಇವರ ಅಮಾನತ್ತನ್ನು ಮುಂದುವರೆಸುವ ಬಗ್ಗೆ.
ಉಲ್ಲೇಖ: ಅಮಾನತ್ತು ಆದೇಶ ಸಂಖ್ಯೆ: ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/05/2025 ದಿನಾಂಕ: 12.12.2025
ಇವರ ಅಮಾನತ್ತನ್ನು ದಿನಾಂಕ 12.03.2026 ರಿಂದ 11.06.2026 ರವರೆಗೆ ಮುಂದುವರೆಸಿ
ಶೇ 50 ರಷ್ಟು ಜೀವನಾಧಾರ ಭತ್ಯೆ ಪಾವತಿಸಲು ಆದೇಶಿಸಿದೆ."""

REVOKE_TEXT = """ಸಂಖ್ಯೆ: ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/31/2026 ದಿನಾಂಕ: 02.04.2026
ವಿಷಯ: ಶ್ರೀ ಕೆ.ರಮೇಶ್ ಇವರ ಅಮಾನತ್ತನ್ನು ತೆರವುಗೊಳಿಸಿ ಸೇವೆಗೆ ಪುನರ್‌ಸ್ಥಾಪಿಸುವ ಬಗ್ಗೆ.
ಇಲಾಖಾ ವಿಚಾರಣೆ ಬಾಕಿ ಇರಿಸಿ ಇವರ ಅಮಾನತ್ತನ್ನು ತಕ್ಷಣದಿಂದ ತೆರವುಗೊಳಿಸಿ
ತಹಶೀಲ್ದಾರರ ಕಛೇರಿ, ಮುಂಡರಗಿ ಇಲ್ಲಿಗೆ ಸ್ಥಳ ನಿಯುಕ್ತಿಗೊಳಿಸಿದೆ."""


def test_element_lists_match_the_sheets():
    assert [e.number for e in elements_for(INVOKE)] == list(range(1, 34))
    assert [e.number for e in elements_for(EXTEND)] == list(range(1, 30))
    assert [e.number for e in elements_for(REVOKE)] == list(range(1, 32))
    assert len({e.key for e in ELEMENTS}) == len(ELEMENTS)
    assert ORDER_TYPES == {
        "Suspension": INVOKE,
        "Suspension extended": EXTEND,
        "Suspension revoked": REVOKE,
    }


def test_extend_order_elements():
    client = FakeClient({
        "order_no": "ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/22/2026",
        "order_date": "10.03.2026",
        "extension_required": "Yes",
        "extension_from": "12.03.2026",
        "extension_to": "11.06.2026",
        "subsistence_allowance": "ಶೇ 50",
        "subsistence_allowance_en": "50%",
        # Only the effective-from / to and reinstatement dates may be
        # "immediate".
        "approval_date": "With immediate effect",
    })
    result = order_elements.extract(EXTEND_TEXT, client, EXTEND)

    assert "continues (extends) the suspension" in client.prompt
    assert '"reinstatement_date"' not in client.prompt
    v = result.values
    assert v["order_no"] == "ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/22/2026"
    assert v["extension_required"] == "Yes"
    assert v["extension_from"] == "12.03.2026"
    assert v["extension_to"] == "11.06.2026"
    assert v["subsistence_allowance"] == "50%"
    assert result.original["subsistence_allowance"] == "ಶೇ 50"
    assert v["approval_date"] is None


def test_revoke_order_elements():
    client = FakeClient({
        "suspension_effective_to": "With immediate effect",
        "reinstatement_order_no": "ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/31/2026",
        "reinstatement_date": "02.04.2026",
        "reinstatement_reason": "ಇಲಾಖಾ ವಿಚಾರಣೆ ಬಾಕಿ ಇರಿಸಿ",
        "reinstatement_reason_en": "Pending departmental enquiry",
        "employees": [{
            "employee_name": "ಕೆ.ರಮೇಶ್",
            "employee_name_en": "K. Ramesh",
            "reinstatement_posting_office": "ತಹಶೀಲ್ದಾರರ ಕಛೇರಿ, ಮುಂಡರಗಿ",
            "reinstatement_posting_office_en": "Tahsildar Office, Mundaragi",
            "reinstatement_posting_office_address": (
                "ಮುಂಡರಗಿ ತಾಲ್ಲೂಕು, ಗದಗ ಜಿಲ್ಲೆ"
            ),
        }],
    })
    result = order_elements.extract(REVOKE_TEXT, client, REVOKE)

    assert "revokes the suspension" in client.prompt
    v = result.values
    assert v["suspension_effective_to"] == "With immediate effect"
    # 02.04.2026 is only the order's date: no date "from which" is printed.
    assert v["reinstatement_date"] is None
    assert v["reinstatement_reason"] == "Pending departmental enquiry"
    [employee] = result.employees
    assert employee.values["employee_name"] == "K. Ramesh"
    assert employee.values["reinstatement_posting_office"] == (
        "Tahsildar Office, Mundaragi"
    )
    # Not in the text.
    assert employee.values["reinstatement_posting_office_address"] is None


def test_prompt_asks_for_every_element_and_no_guessing():
    client = FakeClient({"employees": [{"employee_name": None}]})
    result = order_elements.extract(TEXT, client, INVOKE)

    for element in elements_for(INVOKE):
        assert f'"{element.key}"' in client.prompt
    assert '"employee_name_en"' in client.prompt
    assert '"order_date_en"' not in client.prompt
    assert "Never infer" in client.prompt
    assert "never mix details of different employees" in client.prompt
    # Every order element is present, blank when not found; an employee
    # with nothing found is dropped.
    assert set(result.values) == {
        e.key for e in elements_for(INVOKE) if not e.per_employee
    }
    assert all(value is None for value in result.values.values())
    assert result.employees == []


def test_values_found_in_text_are_kept():
    result = order_elements.extract(TEXT, FakeClient({
        "document_type": "Suspension Order",
        "employees": [{
            "employee_name": "ಸಿ.ವ್ಹಿ.ಇಂಗಳಹಳ್ಳಿ",
            "employee_name_en": "C.V. Ingalahalli",
            "designation": "ಎಎಸ್‌ಐ",
            "designation_en": "ASI",
            "office_name": "ಗದಗ ಶಹರ ಪೊಲೀಸ್‌ ಠಾಣೆ",
        }],
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
    [employee] = result.employees
    assert v["document_type"] == "Suspension Order"
    assert employee.values["employee_name"] == "C.V. Ingalahalli"
    assert employee.original["employee_name"] == "ಸಿ.ವ್ಹಿ.ಇಂಗಳಹಳ್ಳಿ"
    assert employee.values["designation"] == "ASI"
    # No English given: the Kannada value is kept.
    assert employee.values["office_name"] == "ಗದಗ ಶಹರ ಪೊಲೀಸ್‌ ಠಾಣೆ"
    assert v["order_date"] == "26.02.2026"
    # 25.02.2026 is the date of the trap, not printed as a registration.
    assert v["case_registration_date"] is None
    # The text never says "immediately" (ತಕ್ಷಣ); see test_yes_no_... for one
    # that does.
    assert v["suspension_effective_from"] is None
    assert v["reason_for_suspension"] == "Caught in a trap operation"
    # Only the copy-to item found in the text is kept.
    assert v["copy_to"] == "Superintendent of Police, Gadag District"
    # The text never mentions subsistence allowance (ಜೀವನಾಧಾರ).
    assert v["subsistence_allowance_applicable"] is None
    assert v["charge_given_during_suspension"] is None
    assert result.missing_fields() == []


def test_values_not_in_text_are_left_blank():
    result = order_elements.extract(TEXT, FakeClient({
        "employees": [{
            "employee_name": "ರಾಮಣ್ಣ ಕೃಷ್ಣಪ್ಪ ನಾಯಕ",
            "employee_name_en": "Ramanna Krishnappa Nayak",
            "employee_kgid": "1234567",
        }],
        "order_date": "01.01.2026",
        # "Immediately" is only a value for the effective-from date.
        "approval_date": "With immediate effect",
        "document_type": "ಅಮಾನತ್ತು ಆದೇಶ",
    }), INVOKE)

    # Nothing of the employee is in the text, so there is no employee.
    assert result.employees == []
    assert result.values["order_date"] is None
    assert result.values["approval_date"] is None
    assert result.values["document_type"] is None
    assert result.missing_fields() == [
        "order_no", "order_date", "employee_name", "designation",
    ]


# Excerpt of an order about two employees (Suspension_Revoke_Orders_1587364).
TWO_EMPLOYEES = """ವಿಷಯ:- ಶ್ರೀ ಬಿ.ಹೆಚ್.ರಮೇಶ್ ನಾಯ್ಕ್, ಸಿಹೆಚ್‌ಸಿ 227 ಮತ್ತು ಶ್ರೀ ಹೆಚ್.ಪಿ.ವಿಕ್ರಂ,
ಸಿಪಿಸಿ-1097, ಮಾಳೂರು ಪೊಲೀಸ್ ಠಾಣೆ ಇವರುಗಳನ್ನು ಅಮಾನತ್ತಿನಲ್ಲಿರಿಸುವ ಬಗ್ಗೆ
ನಂ: ಸ್ಟಿಬ್ಬಂದಿ(2)ಅಮಾನತ್ತು/ 03 /2026 ದಿನಾಂಕ: 26-02-2026."""


def test_order_about_two_employees():
    result = order_elements.extract(TWO_EMPLOYEES, FakeClient({
        "order_no": "ಸ್ಟಿಬ್ಬಂದಿ(2)ಅಮಾನತ್ತು/ 03 /2026",
        "order_date": "26.02.2026",
        "employees": [
            {"employee_name": "ಬಿ.ಹೆಚ್.ರಮೇಶ್ ನಾಯ್ಕ್",
             "employee_name_en": "B.H. Ramesh Naik",
             "designation": "ಸಿಹೆಚ್‌ಸಿ 227", "designation_en": "CHC 227",
             "office_name": "ಮಾಳೂರು ಪೊಲೀಸ್ ಠಾಣೆ",
             "office_name_en": "Malur Police Station",
             # Handwritten on the scan, not in the OCR text.
             "employee_kgid": "1918354"},
            {"employee_name": "ಹೆಚ್.ಪಿ.ವಿಕ್ರಂ",
             "employee_name_en": "H.P. Vikram",
             "designation": "ಸಿಪಿಸಿ-1097", "designation_en": "CPC-1097",
             "office_name": "ಮಾಳೂರು ಪೊಲೀಸ್ ಠಾಣೆ",
             "office_name_en": "Malur Police Station"},
            "not an employee",
        ],
    }), INVOKE)

    assert [e.values["employee_name"] for e in result.employees] == [
        "B.H. Ramesh Naik", "H.P. Vikram",
    ]
    assert [e.values["designation"] for e in result.employees] == [
        "CHC 227", "CPC-1097",
    ]
    assert result.employees[0].values["employee_kgid"] is None
    assert result.missing_fields() == []

    rows = to_rows(DocumentResult("order.pdf", result, TWO_EMPLOYEES, "api",
                                  doc_type=INVOKE))

    # One row per employee, the order's values on each.
    assert [row["Employee"] for row in rows] == ["1 of 2", "2 of 2"]
    assert [row["Employee Name"] for row in rows] == [
        "B.H. Ramesh Naik", "H.P. Vikram",
    ]
    assert [row["Employee Name (Kannada)"] for row in rows] == [
        "ಬಿ.ಹೆಚ್.ರಮೇಶ್ ನಾಯ್ಕ್", "ಹೆಚ್.ಪಿ.ವಿಕ್ರಂ",
    ]
    assert {row["Order Date"] for row in rows} == {"26.02.2026"}


def test_excel_row_has_every_element_column():
    fields = order_elements.extract(TEXT, FakeClient({
        "employees": [{
            "employee_name": "ಸಿ.ವ್ಹಿ.ಇಂಗಳಹಳ್ಳಿ",
            "employee_name_en": "C.V. Ingalahalli",
        }],
    }), INVOKE)
    result = DocumentResult("order.pdf", fields, TEXT, "api", doc_type=INVOKE)

    title, columns = SHEETS[INVOKE]
    [row] = to_rows(result)

    assert title == "Suspension Invoke Orders"
    assert SHEETS[EXTEND][0] == "Suspension Extend Orders"
    assert SHEETS[REVOKE][0] == "Suspension Revoke Orders"
    assert set(row) == {name for name, _ in columns}
    assert row["Employee"] == "1 of 1"
    assert row["Employee Name"] == "C.V. Ingalahalli"
    assert row["Employee Name (Kannada)"] == "ಸಿ.ವ್ಹಿ.ಇಂಗಳಹಳ್ಳಿ"
    assert row["DDO Code"] == ""


def test_order_without_employees_still_gets_a_row():
    fields = order_elements.extract(TEXT, FakeClient({
        "order_date": "26.02.2026",
    }), INVOKE)
    result = DocumentResult("order.pdf", fields, TEXT, "api", doc_type=INVOKE)

    [row] = to_rows(result)

    assert row["Order Date"] == "26.02.2026"
    assert row["Employee Name"] == ""


# Made-up excerpt with the traps found in the real samples: a vehicle
# number, a constable number and an unlabelled (handwritten) number near
# the name, another officer's post after the name, and no charge sheet.
TRAPS = """ಶ್ರೀ ಜಗ್ಗೇಶ್ ನಾಯ್ಕ, ಸಿ.ಪಿ.ಸಿ.16778, ಮಹದೇವಪುರ ಪೊಲೀಸ್ ಠಾಣೆ, ಬೆಂಗಳೂರು ನಗರ ರವರ ಮೇಲೆ ಪೊಲೀಸ್ ಇನ್ಸ್‌ಪೆಕ್ಟರ್ ವರದಿ.
ಹೊಯ್ಸಳ-236 (ಕೆಎ02ಜಿ4678) ವಾಹನ. ಆರೋಪಗಳು: ...
ಜೀವನಾಧಾರ ಭತ್ಯೆಗೆ ಅರ್ಹರಾಗಿರುತ್ತೀರಿ. ತಕ್ಷಣದಿಂದಲೇ ಜಾರಿಗೆ ಬರುವಂತೆ ಅಮಾನತ್ತುಗೊಳಿಸಿದೆ.
1918354 -> Jaggesh Naik
ಕೆಜಿಐಡಿ ಸಂಖ್ಯೆ: 2560098"""


def _traps(employee: dict, order: dict | None = None):
    return order_elements.extract(TRAPS, FakeClient({
        **(order or {}), "employees": [employee],
    }), INVOKE)


def test_kgid_only_when_labelled():
    result = _traps({"employee_name": "ಶ್ರೀ ಜಗ್ಗೇಶ್ ನಾಯ್ಕ",
                     "employee_kgid": "16778", "dgo_kgid": "1918354"})
    [employee] = result.employees
    assert employee.values["employee_kgid"] is None
    assert employee.values["dgo_kgid"] is None

    result = _traps({"employee_name": "ಶ್ರೀ ಜಗ್ಗೇಶ್ ನಾಯ್ಕ",
                     "employee_kgid": "2560098"})
    assert result.employees[0].values["employee_kgid"] == "2560098"


def test_designation_must_follow_the_name():
    result = _traps({"employee_name": "ಶ್ರೀ ಜಗ್ಗೇಶ್ ನಾಯ್ಕ",
                     "designation": "ಪೊಲೀಸ್ ಇನ್ಸ್‌ಪೆಕ್ಟರ್",
                     "designation_en": "Police Inspector"})
    [employee] = result.employees
    # Honorific removed from the name.
    assert employee.original["employee_name"] == "ಜಗ್ಗೇಶ್ ನಾಯ್ಕ"
    assert employee.values["designation"] is None

    result = _traps({"employee_name": "ಜಗ್ಗೇಶ್ ನಾಯ್ಕ",
                     "employee_name_en": "Shri Jaggesh Naik",
                     "designation": "ಸಿ.ಪಿ.ಸಿ.16778",
                     "designation_en": "CPC 16778"})
    [employee] = result.employees
    assert employee.values["employee_name"] == "Jaggesh Naik"
    assert employee.values["designation"] == "CPC 16778"


def test_yes_no_needs_the_words_in_the_text():
    result = _traps({"employee_name": "ಜಗ್ಗೇಶ್ ನಾಯ್ಕ"}, {
        "subsistence_allowance_applicable": "Yes",
        # No charge sheet in the text, so neither Yes nor No.
        "charge_given_during_suspension": "No",
        "charge_sheet_no": "16778",
        "suspension_effective_from": "ತಕ್ಷಣದಿಂದ",
    })
    v = result.values
    assert v["subsistence_allowance_applicable"] == "Yes"
    assert v["charge_given_during_suspension"] is None
    assert v["charge_sheet_no"] is None
    assert v["suspension_effective_from"] == "With immediate effect"


def test_relieving_memo_is_not_a_revoke_order():
    from go_extractor import suspension

    memo = "ವಿಷಯ:- ಶ್ರೀ ಆನಂದ ಹರಿಜನ್ ಇವರನ್ನು ಕರ್ತವ್ಯದಿಂದ ಬಿಡುಗಡೆಗೊಳಿಸುವ ಕುರಿತು.\nಉಲ್ಲೇಖ:-"
    assert suspension._subject_type(memo) == "Other (suspension related)"


def test_reply_cut_off_keeps_complete_fields():
    reply = ('{"order_no": "ಇವಿ/ಅಮಾನತ್ತು-03/14 /2026", "order_date": '
             '"26.02.2026", "reason_for_suspension": "ಟ್ರ್ಯಾಪ್‌ ಕಾರ್ಯಾ')

    class CutOff(FakeClient):
        def generate(self, prompt):
            return reply

    result = order_elements.extract(TEXT, CutOff({}), INVOKE)

    assert result.values["order_no"] == "ಇವಿ/ಅಮಾನತ್ತು-03/14 /2026"
    assert result.values["order_date"] == "26.02.2026"
    # The cut-off value is left blank, not completed.
    assert result.values["reason_for_suspension"] is None


def test_upside_down_page_is_recognised_as_garbage():
    from go_extractor import ocr

    # What the OCR server returned for an upside-down Kannada page.
    telugu = "కళలు, జంగళితం, కంకరాగ్, కంకరాగ్, కంకరాగ్,\n" * 20
    repeated = "ಆದೇಶ ಸಂಖ್ಯೆ\n" * 20

    assert ocr._garbled(telugu)
    assert ocr._garbled(repeated)
    assert not ocr._garbled(TEXT)
    assert ocr._score(TEXT) > ocr._score(telugu)


def test_order_type_from_file_name():
    from go_extractor.data_elements import type_from_filename

    assert type_from_filename("Suspension_Invoke_Orders_739522.pdf") == INVOKE
    assert type_from_filename("Suspension_Extend_Orders_798860.pdf") == EXTEND
    assert type_from_filename("Suspension_Revoke_Orders_1001165.pdf") == REVOKE
    assert type_from_filename("WP-18329-2025-GO.pdf") is None


def _run(monkeypatch, name: str, text: str):
    from go_extractor import pipeline
    from go_extractor.ocr import OCRResult

    monkeypatch.setattr(pipeline, "extract_text", lambda path: OCRResult(
        text=text, pages=[text], method="api", seconds=0))
    go = pipeline.GOPipeline()
    go.llm = FakeClient({"order_no": "ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/22/2026"})
    return go.process(name)


SUSPENDS = """ವಿಷಯ: ಶ್ರೀ ಕೆ.ರಮೇಶ್ ಇವರನ್ನು ಅಮಾನತ್ತಿನಲ್ಲಿಡುವ ಬಗ್ಗೆ.
ಉಲ್ಲೇಖ: ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/22/2026"""


def test_file_name_decides_the_type(monkeypatch):
    result = _run(monkeypatch, "Suspension_Extend_Orders_1.pdf", SUSPENDS)

    # Named Extend: extracted as Extend although the subject suspends.
    assert result.doc_type == EXTEND
    assert result.fields.order_type == EXTEND
    assert "extension_from" in result.fields.values
    assert result.fields.type_check == (
        "Named Suspension Extend Order, but its subject line reads as "
        "Suspension Invoke Order"
    )
    assert to_rows(result)[0]["Type Check"] == result.fields.type_check

    # Name and subject agree: no warning.
    result = _run(monkeypatch, "Suspension_Invoke_Orders_1.pdf", SUSPENDS)
    assert result.doc_type == INVOKE
    assert result.fields.type_check is None


def test_named_order_without_suspension_words(monkeypatch):
    # e.g. the page with the order was unreadable.
    result = _run(monkeypatch, "Suspension_Revoke_Orders_1.pdf",
                  "ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/22/2026".replace("ಅಮಾನತ್ತು", "ಕ್ರಮ"))

    assert result.doc_type == REVOKE


def test_own_number_only_when_the_subject_says_so():
    memo = """ಸಂಖ್ಯೆ: ತಾಹಿಂವಕ/ಸಿಬ್ಬಂದಿ/ಸಿ.ಆರ್-45/2025-26 ದಿನಾಂಕ:14-10-2025
ವಿಷಯ:- ಶ್ರೀ ಆನಂದ ಹರಿಜನ್ ಇವರನ್ನು ಕರ್ತವ್ಯದಿಂದ ಬಿಡುಗಡೆಗೊಳಿಸುವ ಕುರಿತು.
ಉಲ್ಲೇಖ:- ಆದೇಶ ಸಂಖ್ಯೆ ಹಿಂವಕಇ-1/ಸಿಬ್ಬಂದಿ/ಸಿಆರ್-48/2025-26 ದಿನಾಂಕ: 14-10-2025
ಅನುಮೋದನೆ ಇಲ್ಲ"""
    own = "ತಾಹಿಂವಕ/ಸಿಬ್ಬಂದಿ/ಸಿ.ಆರ್-45/2025-26"
    answers = {"order_no": own, "suspension_order_no": own}

    # A relieving memo filed as an invoke order: not its own number ...
    result = order_elements.extract(memo, FakeClient(answers), INVOKE)
    assert result.values["suspension_order_no"] is None

    # ... but the suspension order it refers to is fine.
    answers["suspension_order_no"] = "ಹಿಂವಕಇ-1/ಸಿಬ್ಬಂದಿ/ಸಿಆರ್-48/2025-26"
    result = order_elements.extract(memo, FakeClient(answers), INVOKE)
    assert result.values["suspension_order_no"]

    # An order whose subject says it suspends is its own suspension order.
    suspends = memo.replace("ಕರ್ತವ್ಯದಿಂದ ಬಿಡುಗಡೆಗೊಳಿಸುವ", "ಅಮಾನತ್ತಿನಲ್ಲಿಡುವ")
    answers["suspension_order_no"] = own
    result = order_elements.extract(suspends, FakeClient(answers), INVOKE)
    assert result.values["suspension_order_no"]


def test_approval_only_when_the_order_mentions_it():
    answers = {"approved_by": "ಪೊಲೀಸ್‌ ಅಧೀಕ್ಷಕರು, ಗದಗ ಜಿಲ್ಲೆ",
               "approval_date": "26.02.2026"}
    result = order_elements.extract(TEXT, FakeClient(answers), INVOKE)

    assert result.values["approved_by"] is None
    assert result.values["approval_date"] is None


def test_named_file_that_is_not_a_suspension_order(monkeypatch):
    bill = "Annexure-A / ಅನುಬಂಧ-ಅ\nDDO Code / ಡಿಡಿಓ ಕೋಡ್ : 16792O\nGross Amount"
    result = _run(monkeypatch, "Suspension_Extend_Orders_1004662.pdf", bill)

    assert result.doc_type == EXTEND
    assert result.fields.type_check == (
        "Named Suspension Extend Order, but the text does not mention "
        "suspension (another kind of document?)"
    )


def test_empty_reply_means_nothing_found():
    class Nothing(FakeClient):
        def generate(self, prompt):
            return "{}"

    result = order_elements.extract(TEXT, Nothing({}), EXTEND)

    assert all(value is None for value in result.values.values())
    assert result.employees == []


def test_both_ocr_answer_formats():
    import json as _json

    import requests

    from go_extractor import ocr

    def response(body: str, content_type: str) -> requests.Response:
        r = requests.Response()
        r._content = body.encode("utf-8")
        r.headers["content-type"] = content_type
        return r

    text = ("===== Source page 1 =====\nಆದೇಶ ಒಂದು\n"
            "===== Source page 2 =====\nಆದೇಶ ಎರಡು\n")
    assert [p.strip() for p in ocr._pages(
        response(text, "text/plain; charset=utf-8"))] == ["ಆದೇಶ ಒಂದು", "ಆದೇಶ ಎರಡು"]

    # One page: no marks.
    assert ocr._pages(response("ಆದೇಶ", "text/plain")) == ["ಆದೇಶ"]

    indic = _json.dumps({"pages": [{"page_number": 1, "markdown": "ಆದೇಶ▁ಒಂದು"}]})
    assert ocr._pages(response(indic, "application/json")) == ["ಆದೇಶ ಒಂದು"]
