"""
Suspension-order extraction, with a fake LLM so the tests run offline.

    python -m pytest tests
"""

import json
from pathlib import Path

from go_extractor import suspension
from go_extractor.ocr import clean_text

FIXTURE = Path(__file__).parent / "fixtures" / "wp_18329_2025.txt"

# Excerpt of samples/suspension/extend/Suspension_Extend_Orders_1015887.pdf
TEXT = """ಆರಕ್ಷಕ ಅಧೀಕ್ಷಕರು, ಗದಗ ಜಿಲ್ಲೆ, ಗದಗ ರವರ ಕಾರ್ಯಾಲಯ
ಸಂಖ್ಯೆ: ಇವಿ/ಅಮಾನತ್ತು-03/14 /2026 ದಿನಾಂಕ: 26.02.2026
: ಅಮಾನತ್ತ ಆದೇಶ :
ವಿಷಯ: ಶ್ರೀ ಸಿ.ವ್ಹಿ.ಇಂಗಳಹಳ್ಳಿ, ಎಎಸ್‌ಐ ಗದಗ ಶಹರ ಪೊಲೀಸ್‌ ಠಾಣೆ
ಇವರನ್ನು ಸೇವೆಯಿಂದ ಅಮಾನತ್ತಿನಲ್ಲಿಡುವ ಕುರಿತು.
ಕರ್ನಾಟಕ ಲೋಕಾಯುಕ್ತ ಪೊಲೀಸ್‌ ಠಾಣೆ ಗದಗ ರವರ ಪ್ರಕರಣ ಸಂಖ್ಯೆ:03/2026
ದಿನಾಂಕ:25.02.2026 ರಂದು ಟ್ರ್ಯಾಪ್‌ ಕಾರ್ಯಾಚರಣೆಯಲ್ಲಿ ಸಿಕ್ಕಿಬಿದ್ದಿರುವುದು
(ರೋಹನ್‌ ಜಗದೀಶ್‌, ಭಾ.ಪೊ.ಸೇ.)
ಪೊಲೀಸ್‌ ಅಧೀಕ್ಷಕರು, ಗದಗ ಜಿಲ್ಲೆ, ಗದಗ"""


class FakeClient:
    name = "fake"

    def __init__(self, answers: dict):
        self.reply = json.dumps(answers, ensure_ascii=False)

    def generate(self, prompt: str) -> str:
        return self.reply


def test_detects_document_type():
    assert suspension.is_suspension_order(TEXT)
    assert not suspension.is_suspension_order(FIXTURE.read_text("utf-8"))


def test_extract_keeps_values_found_in_text():
    result = suspension.extract(TEXT, FakeClient({
        "order_type": "Suspension",
        "order_number": "ಇವಿ/ಅಮಾನತ್ತು-03/14 /2026",
        "order_date": "26-2-2026",
        "employee_name": "ಸಿ.ವ್ಹಿ.ಇಂಗಳಹಳ್ಳಿ",
        "employee_name_en": "C.V. Ingalahalli",
        "employee_designation": "ಎಎಸ್‌ಐ",
        "employee_designation_en": "ASI",
        "employee_office": "ಗದಗ ಶಹರ ಪೊಲೀಸ್‌ ಠಾಣೆ",
        "reason": "Caught in a Lokayukta trap",
        "case_reference": "ಪ್ರಕರಣ ಸಂಖ್ಯೆ:03/2026",
        "effective_from": "With immediate effect",
        "issuing_authority": "ರೋಹನ್‌ ಜಗದೀಶ್‌",
        "issuing_authority_designation": "ಪೊಲೀಸ್‌ ಅಧೀಕ್ಷಕರು, ಗದಗ ಜಿಲ್ಲೆ, ಗದಗ",
        "issuing_authority_designation_en": "ಪೊಲೀಸ್ ಅಧೀಕ್ಷಕರು",
    }))

    assert result.order_type == "Suspension"
    assert result.order_date == "26.02.2026"
    # English shown, the checked Kannada kept alongside.
    assert result.employee_name == "C.V. Ingalahalli"
    assert result.original["employee_name"] == "ಸಿ.ವ್ಹಿ.ಇಂಗಳಹಳ್ಳಿ"
    assert result.employee_designation == "ASI"
    # No English given: the Kannada value is kept.
    assert result.employee_office == "ಗದಗ ಶಹರ ಪೊಲೀಸ್‌ ಠಾಣೆ"
    # "English" still in Kannada script is not used.
    assert result.issuing_authority.designation == (
        "ಪೊಲೀಸ್‌ ಅಧೀಕ್ಷಕರು, ಗದಗ ಜಿಲ್ಲೆ, ಗದಗ"
    )
    # The excerpt never says "immediately" (ತಕ್ಷಣ), so that is not taken.
    assert result.effective_from is None
    assert result.issuing_authority.name == "ರೋಹನ್‌ ಜಗದೀಶ್‌"
    assert result.sources["reason"] == "llm-summary"
    assert result.missing_fields() == []


def test_extract_drops_values_not_in_text():
    result = suspension.extract(TEXT, FakeClient({
        "order_type": "Promotion",
        "order_date": "01.01.2026",
        "employee_name": "ರಾಮಣ್ಣ ಕೃಷ್ಣಪ್ಪ ನಾಯಕ",
        "employee_name_en": "Ramanna Krishnappa Nayak",
    }))

    assert result.order_type is None
    assert result.order_date is None
    assert result.employee_name is None


def test_order_type_wording():
    assert suspension._order_type("suspension revoked and reinstated") == (
        "Suspension revoked"
    )
    assert suspension._order_type("Extension of suspension") == (
        "Suspension extended"
    )


def test_kannada_zero_inside_words():
    assert clean_text("ಸ೦ಖ್ಯೆ ಬೆ೦ಗಳೂರು ೨೦೨೬") == "ಸಂಖ್ಯೆ ಬೆಂಗಳೂರು ೨೦೨೬"


def test_order_type_from_subject_line():
    pay = "ವಿಷಯ:- ಶ್ರೀ ನೀಲಪ್ಪ ಇವರ ಅಮಾನತ್ತಿನ ಅವಧಿಯ ವೇತನ ಸೆಳೆಯುವ ಬಗ್ಗೆ.\nಉಲ್ಲೇಖ:-"
    revoke = "ವಿಷಯ:- ಇವರ ಅಮಾನತ್ತನ್ನು ತೆರವುಗೊಳಿಸಿ ಸೇವೆಗೆ ಪುನರ್‌ಸ್ಥಾಪಿಸುವ ಬಗ್ಗೆ.\nಓದಲಾಗಿದೆ:-"

    assert suspension._subject_type(TEXT) is None
    assert suspension._subject_type(pay) == "Other (suspension related)"
    assert suspension._subject_type(revoke) == "Suspension revoked"


def test_issuing_authority_post_is_not_a_name():
    result = suspension.extract(TEXT, FakeClient({
        "issuing_authority": "ಪೊಲೀಸ್‌ ಅಧೀಕ್ಷಕರು",
    }))

    assert result.issuing_authority.name is None


def test_replacement_character_from_llm_is_repaired():
    text = "ಸಂ: ಭೂಸನಿ.ಸುರಪೂರ/ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/4423/2025-26/05 ದಿನಾಂಕ"

    assert suspension._repair(
        "�ೂಸನಿ.ಸುರಪೂರ/ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/4423/2025-26/05", text
    ) == "ಭೂಸನಿ.ಸುರಪೂರ/ಸಿಬ್ಬಂದಿ/ಅಮಾನತ್ತು/4423/2025-26/05"


def test_service_after_signer_name_is_removed():
    result = suspension.extract(TEXT, FakeClient({
        "issuing_authority": "ರೋಹನ್‌ ಜಗದೀಶ್‌, ಭಾ.ಪೊ.ಸೇ.",
        "issuing_authority_en": "Rohan Jagadish, I.P.S.",
    }))

    assert result.issuing_authority.name == "Rohan Jagadish"
    assert result.original["issuing_authority"] == "ರೋಹನ್‌ ಜಗದೀಶ್‌"
