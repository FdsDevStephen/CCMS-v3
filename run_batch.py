"""
Extract every PDF / image in a folder (and its subfolders) into Excel.

    python run_batch.py                          # choose the folder in a picker
    python run_batch.py "D:\\Orders\\March"        # any folder
    python run_batch.py folder --excel out.xlsx  # another Excel file
    python run_batch.py --workers 4              # 4 documents at a time

Results go to output/suspension_samples.xlsx, saved after each document,
and everything shown on screen also goes to a .log file with the same
name. The log also has what the screen does not show: each OCR request
and LLM call with its time (and tokens), each value a check dropped and
why, and a timing summary at the end. Keep the Excel file closed while
it runs (open a copy to look at the results so far).

Documents already saved there successfully are skipped (failed ones are
tried again), so running it again continues a run that stopped.

With --workers, several documents are processed at the same time; they
are still saved to the one Excel file, one at a time.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# extract.py sets the console to UTF-8 (Kannada) when imported.
from config import settings
from openpyxl import load_workbook

from extract import collect, save, show, summary
from go_extractor.exporter import excel_lock
from go_extractor.pipeline import GOPipeline

sys.stdout.reconfigure(line_buffering=True)

DEFAULT_FOLDER = Path(__file__).resolve().parent / "samples" / "suspension"
DEFAULT_EXCEL = settings.OUTPUT_DIR / "suspension_samples.xlsx"


class Tee:
    """Write to the screen and the log file at the same time."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text: str) -> int:
        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def already_done(excel: Path) -> set[str]:
    """File names saved with Status "Success" on any sheet of the file."""
    if not excel.exists():
        return set()

    done = set()

    # Not while another run is saving it.
    with excel_lock(excel):
        workbook = load_workbook(excel, read_only=True)

        for sheet in workbook:
            rows = sheet.iter_rows(values_only=True)
            header = list(next(rows, ()))

            if "Document" not in header or "Status" not in header:
                continue

            name, status = header.index("Document"), header.index("Status")

            for row in rows:
                if row[name] and row[status] == "Success":
                    done.add(row[name])

        workbook.close()

    return done


def pick_folder() -> str:
    """Choose the folder in a folder picker."""
    from tkinter import Tk, filedialog

    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(
        parent=root, title="Folder with PDF / image files",
        initialdir=DEFAULT_FOLDER, mustexist=True)
    root.destroy()

    if not folder:
        sys.exit("No folder chosen.")

    return folder


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("folder", nargs="?",
                        help="folder with PDF / image files "
                             "(default: choose one in a folder picker)")
    parser.add_argument("--excel", metavar="FILE",
                        help="Excel file (default: "
                             "output/suspension_samples.xlsx)")
    parser.add_argument("--workers", type=int, default=1, metavar="N",
                        help="documents processed at the same time "
                             "(default: 1)")
    args = parser.parse_args()

    if not args.folder:
        args.folder = pick_folder()

    files = collect([args.folder])

    if not files:
        sys.exit(f"No PDF or image files in {args.folder}")

    excel = Path(args.excel) if args.excel else DEFAULT_EXCEL
    log = excel.with_suffix(".log")

    # Continue a run: documents already saved successfully are skipped.
    done = already_done(excel)
    todo = [path for path in files if path.name not in done]

    screen, errors = sys.stdout, sys.stderr

    with open(log, "a" if done else "w", encoding="utf-8") as log_file:
        sys.stdout = Tee(screen, log_file)
        sys.stderr = Tee(errors, log_file)

        # Details (each OCR request and LLM call with its time and tokens,
        # each value dropped by a check and why) go to the log only;
        # warnings also to the screen.
        details = logging.StreamHandler(log_file)
        details.setLevel(logging.INFO)
        warnings = logging.StreamHandler(errors)
        warnings.setLevel(logging.WARNING)
        logging.basicConfig(
            level=logging.INFO, handlers=[details, warnings], force=True,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        # Connection pool chatter from requests.
        logging.getLogger("urllib3").setLevel(logging.WARNING)

        try:
            run(args, files, todo, excel, log, done)
        finally:
            # Back to the screen only before the log file closes.
            logging.getLogger().handlers.clear()
            sys.stdout, sys.stderr = screen, errors


def run(args, files, todo, excel: Path, log: Path, done: set[str]) -> None:
    """Process the files still to do, saving each one to Excel."""

    print(f"Folder : {args.folder} ({len(files)} file(s))")
    print(f"Excel  : {excel}")
    print(f"Log    : {log}")

    total, before = len(files), len(files) - len(todo)

    if done:
        print(f"Continuing: {before} already in the "
              f"Excel file, {len(todo)} to do")

    files = todo

    if not files:
        print("Nothing left to do.")
        return

    workers = max(1, min(args.workers, len(files)))
    print(f"Workers: {workers}")

    # One pipeline per thread: it keeps per-document LLM counters.
    local = threading.local()

    def process(path: Path):
        if not hasattr(local, "pipeline"):
            local.pipeline = GOPipeline()
        return local.pipeline.process(path)

    results, unsaved = [], []
    options = argparse.Namespace(json=False, text=False)
    executor = ThreadPoolExecutor(max_workers=workers)
    start = time.perf_counter()

    try:
        futures = {executor.submit(process, path): path for path in files}

        # Shown and saved here, one at a time, as each document finishes.
        for number, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            print(f"\n[{number}/{len(files)}] {futures[future]}")
            show(result, options)
            unsaved = save(unsaved + [result], excel)
            progress(results, before, total, len(files), start)
    finally:
        # Ctrl+C: drop the documents not started yet.
        executor.shutdown(wait=False, cancel_futures=True)

    summary(results)
    timing_summary(results)

    if unsaved:
        print(f"\nCould not save {len(unsaved)} document(s) to {excel}: "
              "it is open in another program.")
    else:
        print(f"\nSaved to {excel}")


def progress(results: list, before: int, total: int, todo: int,
             start: float) -> None:
    """How many documents of the folder are done, and how long is left."""
    failed = sum(1 for r in results if r.error)
    left = todo - len(results)
    line = (f"  DONE {before + len(results) - failed} of {total} in the "
            f"folder ({len(results)} of {todo} this run")

    if failed:
        line += f", {failed} failed"

    line += f"), {left} left"

    if left:
        minutes = (time.perf_counter() - start) / len(results) * left / 60
        line += f", about {minutes:.0f} min to go"

    print(line)


def timing_summary(results: list) -> None:
    """Total and average OCR / LLM time, and the slowest documents."""
    timed = [r for r in results if "Total" in r.timings]

    if not timed:
        return

    def total(key: str) -> float:
        return sum(r.timings.get(key, 0) for r in timed)

    n = len(timed)
    calls = int(total("LLM calls"))
    print(f"\n  Time for {n} document(s): {total('Total') / 60:.1f} min")
    print(f"    OCR   {total('OCR') / 60:6.1f} min, "
          f"{total('OCR') / n:5.1f}s per document")
    print(f"    LLM   {total('LLM') / 60:6.1f} min, "
          f"{total('LLM') / n:5.1f}s per document, {calls} call(s)")
    print("  Slowest:")

    for r in sorted(timed, key=lambda r: -r.timings["Total"])[:5]:
        print(f"    {r.timings['Total']:6.1f}s  {r.filename} "
              f"(OCR {r.timings.get('OCR', 0):.1f}s, "
              f"LLM {r.timings.get('LLM', 0):.1f}s, {r.pages} page(s))")


if __name__ == "__main__":
    main()
