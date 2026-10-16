"""
Put every sheet of a run_batch.py Excel file on one sheet, in a new file.

    python one_sheet.py                    # output/suspension_samples.xlsx
    python one_sheet.py results.xlsx       # any run_batch.py Excel file

Writes <name>_one_sheet.xlsx next to it (replaced each time), with an
"Order Type" column saying which sheet each row came from. Columns only
some order types have are left empty for the others. A failed attempt
is kept only for a document that never succeeded (its latest one).
"""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from config import settings
from go_extractor.exporter import SHEETS, excel_lock

WIDTHS = {name: width for _, columns in SHEETS.values()
          for name, width in columns}
TAIL = ["Status", "Processed At"]


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        settings.OUTPUT_DIR / "suspension_samples.xlsx")
    target = source.with_name(f"{source.stem}_one_sheet.xlsx")

    # Read under the lock: a run may be saving to it.
    with excel_lock(source):
        workbook = load_workbook(source, read_only=True)
        sheets = []

        for sheet in workbook:
            rows = sheet.iter_rows(values_only=True)
            header = list(next(rows, ()))
            body = [dict(zip(header, row)) for row in rows
                    if any(value is not None for value in row)]
            sheets.append((sheet.title, header, body))

        workbook.close()

    succeeded = {row["Document"] for _, _, body in sheets for row in body
                 if row.get("Status") == "Success"}

    # English columns, then Status / Processed At, then Kannada ones,
    # each in the order the sheets have them.
    english, kannada = [], []

    for _, header, _ in sheets:
        for name in header:
            if not name or name in TAIL or name in english + kannada:
                continue
            (kannada if name.endswith("(Kannada)") else english).append(name)

    columns = ["Order Type", *english, *TAIL, *kannada]

    rows, failures = [], {}

    for title, _, body in sheets:
        for row in body:
            if row.get("Status") == "Success":
                rows.append((title, row))
            elif row["Document"] not in succeeded:
                # The latest failed attempt only.
                failures[row["Document"]] = ("Failed", row)

    rows += failures.values()

    out = Workbook()
    sheet = out.active
    sheet.title = "All Orders"
    sheet.append(columns)

    for kind, row in rows:
        sheet.append([kind, *(row.get(name) for name in columns[1:])])

    count = len(rows)

    for index, name in enumerate(columns, start=1):
        sheet.cell(row=1, column=index).font = Font(bold=True)
        sheet.column_dimensions[get_column_letter(index)].width = (
            WIDTHS.get(name, 24 if name == "Order Type" else 18))

    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = sheet.dimensions
    out.save(target)

    print(f"{count} row(s), {len(columns)} column(s) -> {target}")


if __name__ == "__main__":
    main()
