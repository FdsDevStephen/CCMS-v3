# GovtCase

Extracts case details from Karnataka **Government Orders** that authorise a
Government Advocate to appear in a court case. Works on PDFs (digital or
scanned) and scanned images, in English and Kannada.

| # | Field                                                            | Example                                                                          |
| - | ---------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| 1 | File No.                                                         | ED 02 UTT 2025                                                                   |
| 2 | Govt. Order No. & Date                                           | ED 02 UTT 2025 · 07.07.2025                                                     |
| 3 | Case No.                                                         | W.P.No. 18329/2025                                                               |
| 4 | Litigant (Petitioner)                                            | Dr.Prasanna Kumar S/o Late Halappa                                               |
| 5 | Respondent                                                       | The State of Karnataka & others (+ the numbered respondents represented)         |
| 6 | Government Advocate                                              | Government Advocate, Hon'ble High Court of Karnataka, Bengaluru Bench, Bengaluru |
| 7 | Litigation Conducting Officer (+ Swearing Officer, phone, email) | The Under Secretary to Government, Higher Education (University-2) …            |
| 8 | Issuing Authority                                                | Fairoza H. Ukkali — Head of Legal Cell & Ex-officio Deputy Secretary …         |

## Suspension orders

Kannada / English orders that suspend a government servant, extend a
suspension, or revoke it (samples in `samples/suspension/`). They are
detected automatically (the word ಅಮಾನತ / "suspension", and none of the
Government Advocate wording) and written to a separate
**Suspension Orders** sheet with these fields:

| Field | Notes |
| --- | --- |
| Order Type | Suspension · Suspension extended · Suspension revoked · Other (suspension related) — from the content, not the file name |
| Order No. & Date | of this order, not of the referenced letters |
| Employee Name, Designation, Office | in English (names transliterated, posts translated) |
| Reason | one-line English summary by the LLM (not checked against the text) |
| Rule Invoked, Case Reference | in English |
| Effective From | DD.MM.YYYY or "With immediate effect" |
| Issuing Authority (+ Designation) | in English |

The LLM copies each value as written (usually Kannada) and gives an
English version in the same call. The written value must be found in the
OCR text; only then is its English version used. The written values are
kept in "(Kannada)" columns at the end of the sheet and under "As written
in the order" in the app, for checking against the scan.

The wording differs by department, so the LLM reads the whole order;
values it copies must appear in the OCR text and dates must match a date
in the text, otherwise they are left empty. These need an LLM
(`LLM_PROVIDER` other than `none`).

### Invoke / Extend / Revoke orders — HRMS2 data elements

Suspension, extension and revocation orders are extracted with the HRMS2
data elements (`HRMS2_Disciplinary_Suspension_Data_Elements_Ver2.0.xlsx`):
the 25 Common and Disciplinary elements (ALL) plus those marked for the
order type, each written to its own sheet.

| Order type | Own elements | Sheet |
| --- | --- | --- |
| Suspension Invoke Order | 26-33: suspension order no. / date, effective from, reason, lien post, subsistence allowance (Yes/No), charge given (Yes/No), charge sheet no. | Suspension Invoke Orders |
| Suspension Extend Order | 26-29: extension required (Yes/No), extension from / to, subsistence allowance % | Suspension Extend Orders |
| Suspension Revoke Order | 26-31: suspension effective to, reinstatement order no. / date, posting office / address, reinstatement reason | Suspension Revoke Orders |

The elements are listed in `go_extractor/data_elements.py` and read by
`go_extractor/order_elements.py`. A value is kept only when the order
states it: copied values must be found in the OCR text, dates must match
a date in the text, and Yes / No fields are blank unless the order says
so. Anything else stays blank rather than being inferred.

An order can be about several employees. The employee elements (name,
designation, office, DDO code, KGIDs, cadre, posting, lien
post, reinstatement posting office / address) are read once per employee
named, and each employee gets their own Excel row ("Employee 1 of 3")
with the order's values repeated on it. In JSON they are in an
`"employees"` list.

The order type comes from the **file name** when it names one
(`Suspension_Invoke_Orders_…`, `…_Extend_…`, `…_Revoke_…`), whatever the
order says. If the subject line (ವಿಷಯ) reads as another type, the
"Type Check" column (and a warning in the app) says so; the file name's
type is kept. Without a type in the file name, it comes from the subject
line when it says so plainly, else from an extra LLM call.
"Other (suspension related)" orders keep the fields above.

## How it works

```
PDF / image
   │
   ▼
OCR  (go_extractor/ocr.py)
   PDF text layer if every page has one, else the OCR server
   (OCR_API_URL); no local OCR
   │
   ▼
Type  suspension order? → LLM reads the whole order (go_extractor/suspension.py)
   │  otherwise (Government Advocate GO):
   ▼
Rules  (go_extractor/rules.py)
   regexes anchored on the GO template wording; Kannada digits → 0-9
   │
   ▼  only for fields still empty
LLM fallback  (go_extractor/llm/)
   hosted / OpenAI-compatible / Ollama; every answer must appear in the text
   │
   ▼
Results → Streamlit UI · Excel (output/go_extractions.xlsx) · JSON
```

The sample GO is fully extracted by the rules in ~3 s, with no LLM call.
The LLM is a safety net for wording the rules don't cover, and its answers
are discarded unless they are found in the document text.

## Project structure

```
GovtCase/
├── app.py                  Streamlit app
├── extract.py              command-line tool
├── config/settings.py      all settings, read from .env
├── go_extractor/
│   ├── models.py           GOExtraction, LitigationOfficer, IssuingAuthority, DocumentResult
│   ├── ocr.py              PDF/image → text (English + Kannada)
│   ├── rules.py            rule-based extraction, one section per field
│   ├── suspension.py       suspension orders (LLM + validation)
│   ├── llm/clients.py      generate / openai / ollama clients
│   ├── llm/fallback.py     fills missing fields + validates against the text
│   ├── pipeline.py         OCR → rules → LLM, logs each step with timing
│   └── exporter.py         Excel rows / download
├── tests/                  python -m pytest tests
└── samples/                example GO
```

## Setup

1. OCR: scanned PDFs and images are read by the OCR server at
   `OCR_API_URL` (default `http://101.53.140.252/ocr`); nothing needs
   installing for OCR.
2. `pip install -r requirements.txt`
3. `copy .env.example .env` and adjust if needed (servers, LLM).

## Usage

```
streamlit run app.py

python extract.py samples/WP-18329-2025-GO.pdf
python extract.py path\to\folder --excel      # append all to Excel
python extract.py samples\suspension          # subfolders are included
python extract.py file.pdf --json             # JSON output
python extract.py file.pdf --text             # also show the OCR text
```

## LLM settings (`.env`)

| `LLM_PROVIDER`       | Uses                                                                                  |
| ---------------------- | ------------------------------------------------------------------------------------- |
| `generate` (default) | hosted`/generate` server at `GENERATE_URL`                                        |
| `openai`             | any OpenAI-compatible API (`OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_API_KEY`) |
| `ollama`             | local Ollama (`OLLAMA_MODEL`)                                                       |
| `none`               | rules only                                                                            |

Keep `GENERATE_REPETITION_PENALTY=1.0` — the hosted vLLM server's default
(1.15) stops the model copying names from the text. Set
`GENERATE_NO_THINK=true` only for Qwen3 `/generate` servers. The default
`GENERATE_URL` is `.../vllm/generate_greedy` (Qwen3-14B, 32k context),
which already runs with temperature 0, thinking off and repetition penalty 1.0.

## Notes

- The rules were built from one English sample. Kannada-language GOs are
  read by the OCR and their numbers/dates/case numbers are matched, but
  names and officers in Kannada rely on the LLM fallback until Kannada
  samples are available to write rules from.
- Add new sample GOs to `samples/` and a test to `tests/test_rules.py`
  whenever a new wording is found.
