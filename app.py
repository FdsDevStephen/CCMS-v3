"""
GovtCase - Streamlit app.

    streamlit run app.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="GovtCase",
    page_icon="📜",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from config import settings
from go_extractor.data_elements import ORDER_TYPES, elements_for
from go_extractor.exporter import append_to_excel, to_excel_bytes, to_row
from go_extractor.models import DocumentResult
from go_extractor.ocr import IMAGE_SUFFIXES, check_languages
from go_extractor.pipeline import GOPipeline

UPLOAD_TYPES = ["pdf"] + sorted(s.lstrip(".") for s in IMAGE_SUFFIXES)

FIELD_ROWS = [
    ("1. File No.", "File No."),
    ("2. Govt. Order No.", "Govt. Order No."),
    ("   Govt. Order Date", "Govt. Order Date"),
    ("3. Case No.", "Case No."),
    ("4. Litigant (Petitioner)", "Litigant (Petitioner)"),
    ("5. Respondent", "Respondent"),
    ("   Respondents Represented", "Respondents Represented"),
    ("6. Government Advocate", "Government Advocate"),
    ("7. Litigation Conducting Officer", "Litigation Conducting Officer"),
    ("   Swearing Officer", "Swearing Officer"),
    ("   Phone", "LCO Phone"),
    ("   Email", "LCO Email"),
    ("8. Issuing Authority", "Issuing Authority"),
    ("   Designation", "Issuing Authority Designation"),
]

SUSPENSION_FIELD_ROWS = [
    ("1. Order Type", "Order Type"),
    ("2. Order No.", "Order No."),
    ("   Order Date", "Order Date"),
    ("   Effective From", "Effective From"),
    ("3. Employee Name", "Employee Name"),
    ("   Designation", "Designation"),
    ("   Office", "Office"),
    ("4. Reason", "Reason"),
    ("5. Rule Invoked", "Rule Invoked"),
    ("6. Case Reference", "Case Reference"),
    ("7. Issuing Authority", "Issuing Authority"),
    ("   Designation", "Issuing Authority Designation"),
]


@st.cache_resource
def get_pipeline() -> GOPipeline:
    return GOPipeline()


def process(uploaded) -> DocumentResult:
    suffix = Path(uploaded.name).suffix.lower()

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"upload{suffix}"
        path.write_bytes(uploaded.getvalue())
        return get_pipeline().process(path, filename=uploaded.name)


def show_result(result: DocumentResult) -> None:
    st.subheader(result.filename)

    if result.error:
        st.error(f"Could not process this document: {result.error}")
        return

    row = to_row(result)

    with st.container(border=True):
        if result.doc_type in ORDER_TYPES.values():
            rows = [(f"{e.number}. {e.label}", e.label)
                    for e in elements_for(result.doc_type)]
        elif result.doc_type == "suspension":
            rows = SUSPENSION_FIELD_ROWS
        else:
            rows = FIELD_ROWS

        for label, column in rows:
            left, right = st.columns([1, 3])
            left.markdown(f"**{label.strip()}**" if not label.startswith(" ")
                          else f"&nbsp;&nbsp;&nbsp;{label.strip()}")
            value = row[column]
            right.markdown(value.replace("\n", "  \n") if value else "—")

    missing = result.fields.missing_fields()

    if missing:
        st.warning("Not found: " + ", ".join(m.replace("_", " ") for m in missing))

    if result.doc_type in ORDER_TYPES.values():
        st.caption(f"{result.doc_type} · Values are English versions by the "
                   "LLM of text checked against the document; fields the "
                   "order does not state are left blank.")
    elif result.doc_type == "suspension":
        st.caption("Suspension order · Values are English versions by the "
                   "LLM of text checked against the document; Reason is an "
                   "English summary.")

    if result.doc_type != "advocate":
        originals = {column: value for column, value in row.items()
                     if column.endswith("(Kannada)") and value}

        if originals:
            with st.expander("As written in the order"):
                for column, value in originals.items():
                    left, right = st.columns([1, 3])
                    left.markdown(f"**{column.removesuffix(' (Kannada)')}**")
                    right.markdown(value.replace("\n", "  \n"))

    timing = " · ".join(f"{k} {v:.1f}s" for k, v in result.timings.items())
    st.caption(f"Text: {result.ocr_method} · {timing}")

    with st.expander("Extracted text"):
        st.text(result.text)


# ==========================================================
# PAGE
# ==========================================================

st.title("📜 GovtCase")
st.caption(
    "Extract details from Karnataka Government Orders and suspension "
    "orders (English & Kannada) — PDF or scanned image."
)

missing_langs = check_languages()

if missing_langs:
    st.error(
        f"Tesseract is missing language data: {', '.join(missing_langs)}. "
        f"Install it into Tesseract's tessdata folder."
    )

files = st.file_uploader(
    "Upload Government Orders or suspension orders",
    type=UPLOAD_TYPES,
    accept_multiple_files=True,
)

if "results" not in st.session_state:
    st.session_state.results = []

if files and st.button(
    f"⚡ Extract from {len(files)} document{'s' if len(files) > 1 else ''}",
    type="primary",
    use_container_width=True,
):
    results = []
    progress = st.progress(0.0, text="Starting...")

    for index, uploaded in enumerate(files, start=1):
        progress.progress((index - 1) / len(files),
                          text=f"Processing {uploaded.name} ({index}/{len(files)})")
        results.append(process(uploaded))

    progress.progress(1.0, text="Done")
    st.session_state.results = results

    try:
        append_to_excel(results, settings.EXCEL_PATH)
        st.success(f"Saved to {settings.EXCEL_PATH.name}")
    except PermissionError:
        st.warning(
            f"Could not update {settings.EXCEL_PATH.name} — close it in Excel "
            "and try again. You can still download the results below."
        )

results: list[DocumentResult] = st.session_state.results

if results:
    failed = sum(1 for r in results if r.error)
    st.divider()

    left, right = st.columns(2)
    left.download_button(
        "⬇️ Download Excel",
        data=to_excel_bytes(results),
        file_name="go_extractions.xlsx",
        use_container_width=True,
    )
    right.download_button(
        "⬇️ Download JSON",
        data=json.dumps(
            [{"document": r.filename, "type": r.doc_type, "error": r.error,
              **r.fields.to_dict()}
             for r in results],
            indent=2, ensure_ascii=False,
        ),
        file_name="go_extractions.json",
        mime="application/json",
        use_container_width=True,
    )

    if failed:
        st.error(f"{failed} of {len(results)} document(s) failed.")

    for result in results:
        show_result(result)
