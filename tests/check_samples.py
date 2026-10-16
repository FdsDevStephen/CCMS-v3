"""
Run the real pipeline (OCR server + LLM) on the sample orders and compare
every field with the answer key in tests/expected_samples.json.

    python -m tests.check_samples                 # all documents
    python -m tests.check_samples 802361 1587364  # only files matching

Each document's OCR text is kept in output/sample_ocr/, so a second run
only calls the LLM (delete the folder to OCR again).

Each field is reported as
    OK        matches the key
    INFERRED  filled, but the document does not state it (must be blank)
    MISSED    blank, but the document states it
    DIFFERENT filled with something else than the key
    REVIEW    the key accepts blank or a value here; shown for a look
Exits with 1 if any document has INFERRED, MISSED, DIFFERENT or the wrong
type. Not run by pytest: it needs the servers and takes minutes.
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

from rapidfuzz import fuzz

from config import settings
from go_extractor import ocr
from go_extractor.pipeline import GOPipeline

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = Path(__file__).resolve().parent.parent
KEY = Path(__file__).with_name("expected_samples.json")
REPORT = settings.OUTPUT_DIR / "sample_check.json"

# Loose match for English text (transliterations differ: Mohana / Mohan).
MIN_SIMILARITY = 60

# Compared exactly.
_EXACT = re.compile(r"date|effective|extension_(from|to)|applicable$|"
                    r"required$|charge_given")

BAD = ("INFERRED", "MISSED", "DIFFERENT")

OCR_CACHE = settings.OUTPUT_DIR / "sample_ocr"
_server_pages = ocr._api_pages


def _cached_pages(path: Path) -> list[str]:
    """The OCR server's pages, kept per file so re-runs skip OCR."""
    cache = OCR_CACHE / f"{path.stem}.json"

    if cache.exists():
        return json.loads(cache.read_text("utf-8"))

    pages = _server_pages(path)
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(pages, ensure_ascii=False), "utf-8")
    return pages


def _norm(value: str) -> str:
    return re.sub(r"[^\w/]+", " ", value.lower()).strip()


def compare(key: str, expected, actual) -> str:
    if expected == "?":
        return "REVIEW" if actual else "OK"
    if expected is None:
        return "INFERRED" if actual else "OK"
    if not actual:
        return "MISSED"
    # Several acceptable answers (e.g. the incident or the misconduct
    # finding as the reason, both stated in the order).
    if isinstance(expected, list):
        return ("OK" if any(compare(key, e, actual) == "OK" for e in expected)
                else "DIFFERENT")
    if _EXACT.search(key):
        return "OK" if _norm(expected) == _norm(actual) else "DIFFERENT"

    similarity = max(fuzz.token_set_ratio(_norm(expected), _norm(actual)),
                     fuzz.partial_ratio(_norm(actual), _norm(expected)))
    return "OK" if similarity >= MIN_SIMILARITY else "DIFFERENT"


def check_values(
    label: str, expected: dict, actual: dict, strict: bool,
) -> list[tuple]:
    """(where, key, status, expected, actual) for every field."""
    keys = list(actual) if strict else list(expected)
    lines = []

    for key in keys:
        want = expected.get(key)  # not listed -> must be blank
        got = actual.get(key)
        lines.append((label, key, compare(key, want, got), want, got))

    return lines


def check(document: dict, result) -> list[tuple]:
    lines = []

    if result.error:
        return [("document", "error", "MISSED", "", result.error)]

    if result.doc_type != document["doc_type"]:
        lines.append(("document", "type", "DIFFERENT",
                      document["doc_type"], result.doc_type))
        return lines

    if "legacy" in document:
        fields = result.fields.to_dict()
        authority = fields.pop("issuing_authority")
        fields["issuing_authority_name"] = authority["name"]
        fields["issuing_authority_designation"] = authority["designation"]
        return check_values("order", document["legacy"], fields, False)

    f = result.fields
    lines += check_values("order", document["order"], f.values, True)

    expected_employees = document["employees"]

    if len(f.employees) != len(expected_employees):
        lines.append(("document", "employees", "DIFFERENT",
                      len(expected_employees), len(f.employees)))

    for number, (want, got) in enumerate(
        zip(expected_employees, f.employees), start=1
    ):
        lines += check_values(f"employee {number}", want, got.values, True)

    return lines


def main() -> None:
    documents = json.loads(KEY.read_text("utf-8"))["documents"]
    only = [arg for arg in sys.argv[1:] if not arg.startswith("--")]

    ocr._api_pages = _cached_pages

    if only:
        documents = [d for d in documents
                     if any(part in d["file"] for part in only)]

    pipeline = GOPipeline()
    report, failed = [], 0

    for document in documents:
        path = BASE / document["file"]
        print("\n" + "=" * 72 + f"\n  {path.name}\n" + "=" * 72)

        if not path.exists():
            print("  file not found")
            failed += 1
            continue

        result = pipeline.process(path)
        lines = check(document, result)
        bad = [line for line in lines if line[2] in BAD]

        for where, key, status, want, got in lines:
            if status == "OK" and not got:
                continue
            print(f"  {status:<9} {where:<11} {key}")
            if status != "OK":
                print(f"            expected: {want!r}")
            print(f"            got:      {got!r}")

        print(f"\n  {'FAIL' if bad else 'PASS'}: {len(bad)} problem(s)")
        failed += bool(bad)
        report.append({
            "file": document["file"],
            "doc_type": result.doc_type,
            "error": result.error,
            "fields": result.fields.to_dict(),
            "checks": [dict(zip(("where", "key", "status", "expected",
                                 "got"), line)) for line in lines],
            "text": result.text,
        })

    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"\n{len(documents) - failed} of {len(documents)} document(s) "
          f"passed. Full output: {REPORT}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
