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
            print(f"[GO] {name} | {message}", flush=True)

        start = time.perf_counter()

        try:
            log("OCR ...")
            ocr = extract_text(path)
            timings["OCR"] = ocr.seconds
            log(f"OCR done in {ocr.seconds:.1f}s ({ocr.method}, "
                f"{len(ocr.pages)} page(s), {len(ocr.text)} chars)")

            if not ocr.text.strip():
                raise ValueError("No text could be read from the document.")

            if suspension.is_suspension_order(ocr.text):
                return self._suspension(name, ocr, timings, start, log)

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

            timings["Total"] = time.perf_counter() - start
            log(f"Total {timings['Total']:.1f}s")

            return DocumentResult(
                filename=name,
                fields=fields,
                text=ocr.text,
                ocr_method=ocr.method,
                timings=timings,
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

    def _suspension(self, name, ocr, timings, start, log) -> DocumentResult:
        if self.llm is None:
            raise ValueError(
                "Suspension orders need the LLM: set LLM_PROVIDER in .env."
            )

        # The order type decides the fields: from the subject line, else
        # from the LLM's reading of the whole order.
        step = time.perf_counter()
        fields = None
        order_type = suspension._subject_type(ocr.text)

        if order_type is None:
            log(f"Suspension order, LLM ({self.llm.name}) ...")
            fields = suspension.extract(ocr.text, self.llm, ocr.pages)
            order_type = fields.order_type

        doc_type = data_elements.ORDER_TYPES.get(order_type)

        if doc_type:
            log(f"{doc_type}, data elements, LLM ({self.llm.name}) ...")
            fields = order_elements.extract(
                ocr.text, self.llm, doc_type, ocr.pages
            )
        else:
            doc_type = "suspension"

            if fields is None:
                log(f"Suspension order, LLM ({self.llm.name}) ...")
                fields = suspension.extract(ocr.text, self.llm, ocr.pages)

        timings["LLM"] = time.perf_counter() - step
        log(f"LLM done in {timings['LLM']:.1f}s, missing: "
            f"{', '.join(fields.missing_fields()) or 'none'}")

        timings["Total"] = time.perf_counter() - start
        log(f"Total {timings['Total']:.1f}s")

        return DocumentResult(
            filename=name,
            fields=fields,
            text=ocr.text,
            ocr_method=ocr.method,
            timings=timings,
            doc_type=doc_type,
        )
