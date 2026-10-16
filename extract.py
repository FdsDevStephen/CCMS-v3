"""
Command-line extraction.

    python extract.py samples/WP-18329-2025-GO.pdf
    python extract.py folder/with/gos --excel        # Excel in output/
    python extract.py folder --excel results.xlsx    # Excel file of your choice
    python extract.py samples/suspension             # folders are searched recursively
    python extract.py file.pdf --json                # print JSON only
    python extract.py file.pdf --text                # also print the OCR text

With --excel each document is saved as soon as it is done, so a long run
that stops half way keeps what it finished.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from config import settings
from go_extractor.exporter import SHEETS, append_to_excel, to_rows
from go_extractor.models import DocumentResult
from go_extractor.ocr import SUPPORTED_SUFFIXES
from go_extractor.pipeline import GOPipeline


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []

    for raw in paths:
        path = Path(raw)

        if path.is_dir():
            files += sorted(
                p for p in path.rglob("*")
                if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
            )
        elif path.exists():
            files.append(path)
        else:
            print(f"Not found: {path}")

    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("paths", nargs="+", help="PDF/image files or folders")
    parser.add_argument(
        "--excel", nargs="?", const="", metavar="FILE",
        help="save results to Excel, one row per document (per employee "
             "for suspension orders), saved after each document; FILE "
             "defaults to a new output/extractions_<date>_<time>.xlsx")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument("--text", action="store_true", help="print OCR text too")
    args = parser.parse_args()

    files = collect(args.paths)

    if not files:
        sys.exit("No PDF or image files to process.")

    excel = None

    if args.excel is not None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        excel = Path(args.excel) if args.excel else (
            settings.OUTPUT_DIR / f"extractions_{stamp}.xlsx"
        )
        print(f"{len(files)} file(s); results go to {excel}")

    pipeline = GOPipeline()
    results: list[DocumentResult] = []
    unsaved: list[DocumentResult] = []

    for number, path in enumerate(files, start=1):
        print(f"\n[{number}/{len(files)}] {path}", flush=True)
        result = pipeline.process(path)
        results.append(result)
        show(result, args)

        if excel:
            unsaved = save(unsaved + [result], excel)

    summary(results)

    if excel:
        if unsaved:
            print(f"\nCould not save {len(unsaved)} document(s) to {excel}: "
                  "it is open in another program.")
        else:
            print(f"\nSaved to {excel}")


def show(result: DocumentResult, args: argparse.Namespace) -> None:
    if args.json:
        print(json.dumps(
            {"document": result.filename, "type": result.doc_type,
             "error": result.error, **result.fields.to_dict()},
            indent=2, ensure_ascii=False,
        ))
        return

    print("\n" + "=" * 72)
    print(f"  {result.filename}")
    print("=" * 72)

    rows = to_rows(result)

    for row in rows:
        if len(rows) > 1:
            print(f"\n  --- Employee {row['Employee']} ---")

        for column, value in row.items():
            if column in ("Document", "Employee", "Processed At"):
                continue
            value = str(value).replace("\n", "\n" + " " * 33)
            print(f"  {column:<30} {value or '-'}")

    if args.text and result.text:
        print("\n--- OCR TEXT ---\n" + result.text)


def save(unsaved: list[DocumentResult], excel: Path) -> list[DocumentResult]:
    """Append the unsaved results; keep them if the file is open in Excel."""
    try:
        append_to_excel(unsaved, excel)
        return []
    except PermissionError:
        print(f"  {excel.name} is open in another program; will try again "
              "after the next document", flush=True)
        return unsaved


def summary(results: list[DocumentResult]) -> None:
    failed = [r for r in results if r.error]
    types = Counter(SHEETS[r.doc_type][0] for r in results if not r.error)

    print("\n" + "=" * 72)
    print(f"  {len(results) - len(failed)} of {len(results)} document(s) "
          "processed")

    for doc_type, count in types.most_common():
        print(f"    {count:>4}  {doc_type}")

    for result in failed:
        print(f"  FAILED  {result.filename}: {result.error}")


if __name__ == "__main__":
    main()
