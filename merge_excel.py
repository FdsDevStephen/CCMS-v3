"""
Merge the Excel files of several run_batch.py runs into one.

    python merge_excel.py output/suspension_samples.xlsx output/suspension_part*.xlsx output/suspension_revoke.xlsx

Rows go to the sheet with the same name in the first file (created if
needed), matched by column name. Documents already saved there with
Status "Success" are skipped, so running it twice adds nothing. A copy
of the first file is kept as <name>.bak.xlsx. Run it only when the runs
have finished and the files are closed in Excel.
"""

from __future__ import annotations

import shutil
import sys
from glob import glob
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment

from go_extractor.exporter import SHEETS, _sheet, excel_lock
from run_batch import already_done

COLUMNS = {title: columns for title, columns in SHEETS.values()}


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)

    target = Path(sys.argv[1])
    # PowerShell does not expand wildcards itself.
    sources = [Path(p) for arg in sys.argv[2:] for p in sorted(glob(arg)) or [arg]]
    sources = [p for p in sources if p.resolve() != target.resolve()]

    done = already_done(target)

    # Saves from runs still going wait until the merge is done.
    with excel_lock(target):
        shutil.copy2(target, target.with_suffix(".bak.xlsx"))
        workbook = load_workbook(target)

        for source in sources:
            if not source.exists():
                print(f"Not found: {source}")
                continue

            added, skipped = set(), set()

            for sheet in load_workbook(source, read_only=True):
                rows = sheet.iter_rows(values_only=True)
                header = list(next(rows, ()))

                if "Document" not in header:
                    continue

                out = _sheet(workbook, sheet.title, COLUMNS.get(sheet.title, []))
                out_header = [cell.value for cell in out[1]]

                # Columns only in the source go on the end.
                for name in header:
                    if name and name not in out_header:
                        out.cell(row=1, column=len(out_header) + 1, value=name)
                        out_header.append(name)

                for row in rows:
                    values = dict(zip(header, row))
                    document = values.get("Document")

                    if not document:
                        continue
                    if document in done:
                        skipped.add(document)
                        continue

                    out.append([values.get(name) for name in out_header])
                    for cell in out[out.max_row]:
                        cell.alignment = Alignment(wrap_text=True, vertical="top")
                    added.add(document)

            print(f"{source}: {len(added)} document(s) added, "
                  f"{len(skipped)} already in {target.name}")

        workbook.save(target)
    print(f"Saved {target} (backup: {target.with_suffix('.bak.xlsx').name})")


if __name__ == "__main__":
    main()
