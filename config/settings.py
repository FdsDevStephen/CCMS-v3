"""
All settings for GovtCase, read from .env (see .env.example).

    from config.settings import OCR_API_URL, LLM_PROVIDER
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


# ==========================================================
# OCR
# ==========================================================

# Scanned PDFs and images are read by this OCR server; there is no local
# OCR. It answers plain text with "===== Source page N =====" marks
# (IndicOCR's JSON, .../indicocr/ocr_markdown, is read too).
OCR_API_URL: str = os.getenv("OCR_API_URL", "http://101.53.140.252/ocr")
# The server takes ~2-10 s per page; long or busy documents take longer.
OCR_API_TIMEOUT: int = int(os.getenv("OCR_API_TIMEOUT", "900"))

# GOs are 1-2 pages; this only guards against huge uploads.
OCR_MAX_PAGES: int = int(os.getenv("OCR_MAX_PAGES", "10"))

# Use the PDF's own text when every page has at least this many
# characters of it (a digital PDF); otherwise the file goes to OCR.
OCR_MIN_TEXT_LAYER_CHARS: int = int(os.getenv("OCR_MIN_TEXT_LAYER_CHARS", "80"))


# ==========================================================
# LLM (fallback for fields the rules could not find)
# ==========================================================

# none     = rules only
# generate = hosted "/generate" server (GENERATE_URL)
# openai   = any OpenAI-compatible API (OPENAI_BASE_URL / OPENAI_MODEL)
# ollama   = local Ollama model (OLLAMA_MODEL)
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "generate")

# Suspension Invoke Orders return ~58 values, which takes a few minutes.
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "300"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
# Suspension orders return each field in Kannada and English (~800 tokens
# for the 11 suspension fields, more for the 33 invoke-order data elements).
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))

GENERATE_URL: str = os.getenv(
    "GENERATE_URL", "http://101.53.137.13:8004/vllm/generate_greedy"
)
# Must stay 1.0: higher values stop the model copying names from the text.
GENERATE_REPETITION_PENALTY: float = float(
    os.getenv("GENERATE_REPETITION_PENALTY", "1.0")
)
# Qwen3 only: append "/no_think" to skip slow reasoning.
GENERATE_NO_THINK: bool = _bool("GENERATE_NO_THINK", "false")

OPENAI_BASE_URL: str = os.getenv(
    "OPENAI_BASE_URL", "http://101.53.137.13:8004/vllm_openai/v1"
)
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "qwen2.5-3b-instruct")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b-instruct")


# ==========================================================
# OUTPUT
# ==========================================================

OUTPUT_DIR: Path = BASE_DIR / os.getenv("OUTPUT_DIR", "output")
EXCEL_PATH: Path = OUTPUT_DIR / "go_extractions.xlsx"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
