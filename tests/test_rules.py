"""
Tests for rule-based extraction. Run:  python -m pytest tests
"""

from pathlib import Path

import pytest

from go_extractor import rules
from go_extractor.llm.fallback import _appears_in
from go_extractor.ocr import clean_text

FIXTURE = Path(__file__).parent / "fixtures" / "wp_18329_2025.txt"


@pytest.fixture(scope="module")
def sample_text() -> str:
    return clean_text(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sample(sample_text):
    return rules.extract(sample_text)


# ----------------------------------------------------------
# The real sample (OCR text of WP-18329-2025-GO.pdf)
# ----------------------------------------------------------


def test_file_and_order_number(sample):
    assert sample.file_no == "ED 02 UTT 2025"
    assert sample.go_number == "ED 02 UTT 2025"
    assert sample.go_date == "07.07.2025"


def test_case_and_parties(sample):
    assert sample.case_number == "W.P.No. 18329/2025"
    assert sample.petitioner == "Dr.Prasanna Kumar S/o Late Halappa"
    assert sample.respondent == "The State of Karnataka & others"
    assert sample.respondents_represented == [
        "The State of Karnataka, Represented by its Principal Secretary, "
        "Department of Higher Education Department, M.S. Building, "
        "Dr.Ambedkar Road, Bengaluru-560001"
    ]


def test_government_advocate(sample):
    assert sample.government_advocate == (
        "Government Advocate, Hon'ble High Court of Karnataka, "
        "Bengaluru Bench, Bengaluru"
    )


def test_litigation_officer(sample):
    lco = sample.litigation_officer
    assert lco.officer == (
        "The Under Secretary to Government, Higher Education (University-2) "
        "Department of Collegiate Education, Palace Road, Bengaluru-560001"
    )
    assert lco.phone == "080-22032538"
    assert lco.email == "usuniversitysection2@gmail.com"
    assert lco.is_swearing_officer is True


def test_issuing_authority(sample):
    assert sample.issuing_authority.name == "Fairoza H. Ukkali"
    assert sample.issuing_authority.designation.startswith(
        "Head of Legal Cell & Ex-officio Deputy Secretary to Government"
    )


def test_nothing_missing(sample):
    assert sample.missing_fields() == []


# ----------------------------------------------------------
# Wording variations
# ----------------------------------------------------------


def test_preferred_by_against_wording():
    text = ("the W.A.No. 1234/2024 preferred by Sri Ramesh S/o Krishna against "
            "The State of Karnataka on the file of the Hon'ble High Court")
    assert rules.parties(text) == (
        "Sri Ramesh S/o Krishna", "The State of Karnataka"
    )
    assert rules.case_number(text) == "W.A.No. 1234/2024"


def test_named_advocate_directed_to_appear():
    text = ("Sri K. Mohan, Additional Government Advocate, High Court of "
            "Karnataka is hereby directed to appear and defend")
    assert rules.government_advocate(text) == (
        "Sri K. Mohan, Additional Government Advocate, High Court of Karnataka"
    )


def test_several_numbered_respondents():
    text = (
        "on behalf of the following Respondents.\n\n"
        "1) The State of Karnataka, Represented by its Secretary,\n"
        "Revenue Department, Bengaluru.\n"
        "2) The Deputy Commissioner,\nMysuru District, Mysuru.\n\n"
        "The Tahsildar shall be the Litigation Conducting Officer."
    )
    assert rules.respondents_represented(text) == [
        "The State of Karnataka, Represented by its Secretary, "
        "Revenue Department, Bengaluru",
        "The Deputy Commissioner, Mysuru District, Mysuru",
    ]


def test_separate_swearing_officer():
    text = (
        "The Tahsildar, Mysuru Taluk shall be the Litigation Conducting "
        "Officer. The Deputy Commissioner, Mysuru shall be the Swearing Officer."
    )
    lco = rules.litigation_officer(text)
    assert lco.officer == "The Tahsildar, Mysuru Taluk"
    assert lco.is_swearing_officer is False
    assert lco.swearing_officer == "The Deputy Commissioner, Mysuru"


def test_kannada_digits_and_labels():
    text = rules.prepare(
        "ಸರ್ಕಾರಿ ಆದೇಶ ಸಂಖ್ಯೆ: ಕಂಇ ೧೨ ಭೂಸುಧಾ ೨೦೨೫\n"
        "ಬೆಂಗಳೂರು, ದಿನಾಂಕ: ೦೭.೦೭.೨೦೨೫\n"
        "ರಿಟ್ ಅರ್ಜಿ ಸಂಖ್ಯೆ: ೧೮೩೨೯/೨೦೨೫"
    )
    number, date = rules.go_number_and_date(text)
    assert number == "ಕಂಇ 12 ಭೂಸುಧಾ 2025"
    assert date == "07.07.2025"
    assert rules.case_number(text) == "ರಿಟ್ ಅರ್ಜಿ ಸಂಖ್ಯೆ 18329/2025"


def test_phone_bracket_removed_but_title_bracket_kept():
    text = ("The Under Secretary (Lands), Revenue Department (Phone No. "
            "080-1234567, Email: a@b.in) shall be the Litigation Conducting "
            "Officer.")
    lco = rules.litigation_officer(text)
    assert lco.officer == "The Under Secretary (Lands), Revenue Department"
    assert lco.phone == "080-1234567"
    assert lco.email == "a@b.in"


# ----------------------------------------------------------
# LLM answer validation
# ----------------------------------------------------------


def test_llm_values_must_appear_in_text(sample_text):
    assert _appears_in("Dr.Prasanna Kumar S/o Late Halappa", sample_text)
    assert _appears_in("dr. prasanna kumar s/o late halappa", sample_text)
    assert not _appears_in("Sri Ramesh Gowda", sample_text)
    assert not _appears_in("W.P.No. 99999/2024", sample_text)


# ----------------------------------------------------------
# Same GO read by the OCR server (no blank lines, name read twice)
# ----------------------------------------------------------

API_FIXTURE = Path(__file__).parent / "fixtures" / "wp_18329_2025_api.txt"


def test_ocr_server_text():
    result = rules.extract(clean_text(API_FIXTURE.read_text(encoding="utf-8")))

    assert result.missing_fields() == []
    assert result.respondents_represented == [
        "The State of Karnataka, Represented by its Principal Secretary, "
        "Department of Higher Education Department, M.S Building, "
        "Dr.Ambedkar Road, Bengaluru-560001"
    ]
    assert result.issuing_authority.name == "Fairoza H. Ukkali"
    assert result.issuing_authority.designation == (
        "Head of Legal Cell & Ex-officio Deputy Secretary to Government, "
        "Department of Higher Edn., Health & Family Welfare & Medical Edn."
    )
    assert result.litigation_officer.phone == "080-22032538"
