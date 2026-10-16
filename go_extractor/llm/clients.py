"""
LLM clients. All return the model's raw text answer for a prompt.

    generate - hosted "/generate" server: {"prompt", ...} -> {"text"}
    openai   - any OpenAI-compatible chat API (/v1/chat/completions)
    ollama   - local Ollama (/api/chat)
"""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod

import requests

from config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a JSON extraction engine. Return ONLY valid JSON. "
    "Never explain. Never use markdown. Output exactly one JSON object."
)


class LLMClient(ABC):

    name = "llm"

    def __init__(self) -> None:
        self.session = requests.Session()
        # Totals since the last reset (the pipeline resets per document).
        self.calls = 0
        self.seconds = 0.0
        # What the server said about the last call (tokens, timing).
        self.last_info: dict = {}

    def generate(self, prompt: str) -> str:
        last_error = None

        for attempt in range(1, settings.MAX_RETRIES + 1):
            start = time.perf_counter()

            try:
                self.last_info = {}
                reply = clean_reply(self._call(prompt))
                seconds = time.perf_counter() - start
                self.calls += 1
                self.seconds += seconds
                logger.info(
                    "LLM call %d: %.1fs, prompt %d chars, reply %d chars%s",
                    self.calls, seconds, len(prompt), len(reply),
                    "".join(f", {k} {v}" for k, v in self.last_info.items()),
                )
                return reply
            except Exception as e:
                self.seconds += time.perf_counter() - start
                last_error = e
                print(
                    f"[{self.name}] Attempt {attempt}/{settings.MAX_RETRIES} "
                    f"failed: {e}",
                    flush=True,
                )
                if attempt < settings.MAX_RETRIES:
                    time.sleep(2)

        raise RuntimeError(
            f"{self.name}: no response after {settings.MAX_RETRIES} attempts"
        ) from last_error

    @abstractmethod
    def _call(self, prompt: str) -> str:
        ...

    def _post(self, url: str, payload: dict) -> dict:
        response = self.session.post(
            url, json=payload, timeout=settings.REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()


class GenerateClient(LLMClient):
    name = "generate"

    def _call(self, prompt: str) -> str:
        prompt = SYSTEM_PROMPT + "\n\n" + prompt

        if settings.GENERATE_NO_THINK:
            prompt += "\n\n/no_think"

        data = self._post(settings.GENERATE_URL, {
            "prompt": prompt,
            "max_tokens": settings.LLM_MAX_TOKENS,
            "temperature": 0,
            "repetition_penalty": settings.GENERATE_REPETITION_PENALTY,
        })
        self.last_info = {
            key: data[key] for key in
            ("tokens_generated", "inference_ms", "finish_reason", "model")
            if key in data
        }
        return data["text"]


class OpenAIClient(LLMClient):
    name = "openai"

    def __init__(self) -> None:
        super().__init__()

        if settings.OPENAI_API_KEY:
            self.session.headers["Authorization"] = (
                f"Bearer {settings.OPENAI_API_KEY}"
            )

    def _call(self, prompt: str) -> str:
        url = settings.OPENAI_BASE_URL.rstrip("/") + "/chat/completions"

        return self._post(url, {
            "model": settings.OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": settings.LLM_MAX_TOKENS,
        })["choices"][0]["message"]["content"]


class OllamaClient(LLMClient):
    name = "ollama"

    def _call(self, prompt: str) -> str:
        url = settings.OLLAMA_HOST.rstrip("/") + "/api/chat"

        return self._post(url, {
            "model": settings.OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        })["message"]["content"]


_CLIENTS = {
    "generate": GenerateClient,
    "openai": OpenAIClient,
    "ollama": OllamaClient,
}


def get_llm_client() -> LLMClient | None:
    """The configured client, or None when LLM_PROVIDER=none."""
    provider = settings.LLM_PROVIDER.strip().lower()

    if provider in ("", "none", "off"):
        return None

    if provider not in _CLIENTS:
        raise ValueError(
            f"Unknown LLM_PROVIDER '{settings.LLM_PROVIDER}'. "
            f"Use one of: none, {', '.join(_CLIENTS)}."
        )

    return _CLIENTS[provider]()


def clean_reply(text: str) -> str:
    """Drop <think> blocks and ```json fences around the answer."""
    text = re.sub(r"<think>.*?(?:</think>|$)", "", text or "", flags=re.DOTALL)
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()
