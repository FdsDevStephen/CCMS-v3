"""
Command-line extraction.

    python extract.py samples/WP-18329-2025-GO.pdf
    python extract.py folder/with/gos --excel        # also append to Excel
    python extract.py samples/suspension             # folders are searched recursively
    python extract.py file.pdf --json                # print JSON only
    python extract.py file.pdf --text                # also print the OCR text
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from config import settings
from go_extractor.exporter import append_to_excel, to_row
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
    parser.add_argument("--excel", action="store_true",
                        help=f"append results to {settings.EXCEL_PATH}")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument("--text", action="store_true", help="print OCR text too")
    args = parser.parse_args()

    files = collect(args.paths)

    if not files:
        sys.exit("No PDF or image files to process.")

    pipeline = GOPipeline()
    results = [pipeline.process(path) for path in files]

    for result in results:
        if args.json:
            print(json.dumps(
                {"document": result.filename, "type": result.doc_type,
                 "error": result.error, **result.fields.to_dict()},
                indent=2, ensure_ascii=False,
            ))
            continue

        print("\n" + "=" * 72)
        print(f"  {result.filename}")
        print("=" * 72)

        for column, value in to_row(result).items():
            if column in ("Document", "Processed At"):
                continue
            value = str(value).replace("\n", "\n" + " " * 33)
            print(f"  {column:<30} {value or '-'}")

        if args.text and result.text:
            print("\n--- OCR TEXT ---\n" + result.text)

    if args.excel:
        append_to_excel(results, settings.EXCEL_PATH)
        print(f"\nAppended {len(results)} row(s) to {settings.EXCEL_PATH}")


if __name__ == "__main__":
    main()
