"""
GO extraction pipeline: file -> text (OCR) -> document type -> fields ->
DocumentResult. Each step is logged to the terminal with timing.

    advocate GOs     rules, then the LLM fills gaps
    suspension orders  LLM reads the whole order (suspension.py);
                       invoke orders: HRMS2 data elements (order_elements.py)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from go_extractor import data_elements, order_elements, rules, suspension
from go_extractor.llm.clients import get_llm_client
from go_extractor.llm.fallback import fill_missing
from go_extractor.models import DocumentResult, GOExtraction
from go_extractor.ocr import extract_text

logger = logging.getLogger(__name__)


class GOPipeline:

    def __init__(self) -> None:
        self.llm = get_llm_client()

    def process(self, path: str | Path, filename: str | None = None) -> DocumentResult:
        path = Path(path)
        name = filename or path.name
        timings: dict[str, float] = {}

        def log(message: str) -> None:
            print(f"{datetime.now():%H:%M:%S} [GO] {name} | {message}",
                  flush=True)

        start = time.perf_counter()

        if self.llm is not None:
            self.llm.calls, self.llm.seconds = 0, 0.0

        try:
            log("OCR ...")
            ocr = extract_text(path)
            timings["OCR"] = ocr.seconds
            log(f"OCR done in {ocr.seconds:.1f}s ({ocr.method}, "
                f"{len(ocr.pages)} page(s), {len(ocr.text)} chars)")

            if not ocr.text.strip():
                raise ValueError("No text could be read from the document.")

            # A file named as an invoke / extend / revoke order is one, even
            # if its text (e.g. an unreadable page) does not say so.
            named_type = data_elements.type_from_filename(name)

            if named_type or suspension.is_suspension_order(ocr.text):
                return self._suspension(name, ocr, timings, start, log,
                                        named_type)

            step = time.perf_counter()
            fields = rules.extract(ocr.text)
            timings["Rules"] = time.perf_counter() - step
            missing = fields.missing_fields()
            log(f"Rules done in {timings['Rules']:.2f}s, "
                f"missing: {', '.join(missing) or 'none'}")

            if missing and self.llm is not None:
                step = time.perf_counter()
                log(f"LLM ({self.llm.name}) for {len(missing)} field(s) ...")

                try:
                    fields = fill_missing(fields, ocr.text, self.llm)
                except Exception as e:
                    log(f"LLM failed, keeping rule results: {e}")

                timings["LLM"] = time.perf_counter() - step
                log(f"LLM done in {timings['LLM']:.1f}s, still missing: "
                    f"{', '.join(fields.missing_fields()) or 'none'}")

            self._total(timings, start, log)

            # Nothing at all found: not a "Success" with an empty row.
            error = None

            if len(fields.missing_fields()) == len(GOExtraction().missing_fields()):
                error = ("Not recognised as a Government Advocate order or a "
                         "suspension order: nothing could be extracted. "
                         "Check the extracted text.")
                log(error)

            return DocumentResult(
                filename=name,
                fields=fields,
                text=ocr.text,
                ocr_method=ocr.method,
                timings=timings,
                error=error,
                pages=len(ocr.pages),
            )

        except Exception as e:
            logger.exception("Failed to process %s", name)
            log(f"FAILED: {e}")

            return DocumentResult(
                filename=name,
                fields=GOExtraction(),
                text="",
                ocr_method="",
                timings=timings,
                error=str(e),
            )

    def _suspension(self, name, ocr, timings, start, log,
                    named_type=None) -> DocumentResult:
        if self.llm is None:
            raise ValueError(
                "Suspension orders need the LLM: set LLM_PROVIDER in .env."
            )

        # The order type decides the fields: from the file name, else from
        # the subject line, else from the LLM's reading of the whole order.
        step = time.perf_counter()
        fields = None
        subject_type = suspension._subject_type(ocr.text)
        order_type = subject_type

        if named_type:
            order_type = next(t for t, d in data_elements.ORDER_TYPES.items()
                              if d == named_type)
        elif order_type is None:
            log(f"Suspension order, LLM ({self.llm.name}) ...")
            fields = suspension.extract(ocr.text, self.llm, ocr.pages)
            order_type = fields.order_type

        doc_type = data_elements.ORDER_TYPES.get(order_type)

        if doc_type:
            log(f"{doc_type}, data elements, LLM ({self.llm.name}) ...")
            fields = order_elements.extract(
                ocr.text, self.llm, doc_type, ocr.pages
            )

            # Only warnings: the file name's type is kept.
            if named_type and not suspension.is_suspension_order(ocr.text):
                fields.type_check = (
                    f"Named {doc_type}, but the text does not mention "
                    "suspension (another kind of document?)"
                )
                log(fields.type_check)
            elif subject_type and subject_type != order_type:
                subject_doc = data_elements.ORDER_TYPES.get(
                    subject_type, subject_type
                )
                fields.type_check = (
                    f"Named {doc_type}, but its subject line reads as "
                    f"{subject_doc}"
                )
                log(fields.type_check)
        else:
            doc_type = "suspension"

            if fields is None:
                log(f"Suspension order, LLM ({self.llm.name}) ...")
                fields = suspension.extract(ocr.text, self.llm, ocr.pages)

        timings["LLM"] = time.perf_counter() - step
        log(f"LLM done in {timings['LLM']:.1f}s, missing: "
            f"{', '.join(fields.missing_fields()) or 'none'}")

        self._total(timings, start, log)

        return DocumentResult(
            filename=name,
            fields=fields,
            text=ocr.text,
            ocr_method=ocr.method,
            timings=timings,
            doc_type=doc_type,
            pages=len(ocr.pages),
        )

    def _total(self, timings: dict, start: float, log) -> None:
        """Total time, and the LLM calls this document took."""
        timings["Total"] = time.perf_counter() - start
        calls = self.llm.calls if self.llm is not None else 0

        if calls:
            timings["LLM calls"] = calls

        log(f"Total {timings['Total']:.1f}s (OCR {timings.get('OCR', 0):.1f}s, "
            f"LLM {timings.get('LLM', 0):.1f}s in {calls} call(s))")
