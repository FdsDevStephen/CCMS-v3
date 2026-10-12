"""
All settings for GovtCase, read from .env (see .env.example).

    from config.settings import OCR_LANG, LLM_PROVIDER
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

TESSERACT_CMD: str = os.getenv(
    "TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

# api       = OCR server at OCR_API_URL (falls back to Tesseract if it fails)
# tesseract = local Tesseract only
OCR_ENGINE: str = os.getenv("OCR_ENGINE", "api")
OCR_API_URL: str = os.getenv("OCR_API_URL", "http://101.53.140.252/ocr")
OCR_API_TIMEOUT: int = int(os.getenv("OCR_API_TIMEOUT", "300"))

# English + Kannada. Both traineddata files must be in Tesseract's tessdata.
OCR_LANG: str = os.getenv("OCR_LANG", "eng+kan")
OCR_DPI: int = int(os.getenv("OCR_DPI", "300"))

# GOs are 1-2 pages; this only guards against huge uploads.
OCR_MAX_PAGES: int = int(os.getenv("OCR_MAX_PAGES", "10"))

# Use the PDF's own text when a page has at least this many characters.
OCR_MIN_TEXT_LAYER_CHARS: int = int(os.getenv("OCR_MIN_TEXT_LAYER_CHARS", "80"))

OCR_MAX_WORKERS: int = int(
    os.getenv("OCR_MAX_WORKERS", str(min(4, os.cpu_count() or 4)))
)


# ==========================================================
# LLM (fallback for fields the rules could not find)
# ==========================================================

# none     = rules only
# generate = hosted "/generate" server (GENERATE_URL)
# openai   = any OpenAI-compatible API (OPENAI_BASE_URL / OPENAI_MODEL)
# ollama   = local Ollama model (OLLAMA_MODEL)
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "generate")

REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "120"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
# Suspension orders return each field in Kannada and English (~800 tokens).
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "2048"))

GENERATE_URL: str = os.getenv(
    "GENERATE_URL", "http://101.53.137.13:8004/qwen13/generate_greedy"
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
