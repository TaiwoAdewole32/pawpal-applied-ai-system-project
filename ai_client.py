"""AI client boundary for PawPal Sentinel (Phase 4.1 / 4.2).

This module is the only place in the project allowed to talk to the Gemini
SDK. plan_critic.py and repair_agent.py call generate_json; app.py never calls
the SDK directly.

The Gemini dependency is imported lazily inside GeminiAIClient.__init__. This
keeps importing Streamlit/PawPal+ safe when the optional AI dependency is not
installed or when AI configuration is unavailable.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

DEFAULT_MODEL_NAME = "gemini-3.1-flash-lite"


class AIConfigError(RuntimeError):
    """Raised when the AI client is missing configuration or cannot run safely."""


class AIClient(Protocol):
    def generate_json(self, system_prompt: str, user_payload: dict) -> dict:
        ...


def _redact_secret(text: str, secret: str | None) -> str:
    """Strip a literal secret value from an error before logging or display."""
    if not secret or not text:
        return text
    return text.replace(secret, "[REDACTED]")


class GeminiAIClient:
    """Production AIClient backed by the Gemini SDK.

    No SDK import or network call occurs at module import time. Therefore,
    importing app.py cannot fail solely because google-genai is unavailable.
    """

    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise AIConfigError(
                "Missing GEMINI_API_KEY environment variable. "
                "Set it in your shell or .env file to enable AI review features."
            )

        try:
            from google import genai
        except (ImportError, ModuleNotFoundError):
            raise AIConfigError(
                "The google-genai package is not installed. "
                "Run 'pip install -r requirements.txt' to enable AI review features."
            ) from None

        self._api_key = api_key
        self.model_name = (
            model_name
            or os.getenv("PAWPAL_MODEL_NAME")
            or DEFAULT_MODEL_NAME
        )
        try:
            self.client = genai.Client(api_key=api_key)
        except Exception as exc:
            safe_message = _redact_secret(str(exc), api_key)
            raise AIConfigError(
                f"AI client setup failed ({type(exc).__name__}): {safe_message}"
            ) from None

    def generate_json(self, system_prompt: str, user_payload: dict) -> dict:
        """Call Gemini and require a JSON object response."""
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt must be a non-empty string.")
        if not isinstance(user_payload, dict):
            raise TypeError("user_payload must be a dictionary.")

        prompt = f"{system_prompt}\n\n{json.dumps(user_payload)}"

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            text = (response.text or "").strip()
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("AI response must be a JSON object.")
            return parsed
        except Exception as exc:
            safe_message = _redact_secret(str(exc), self._api_key)
            raise AIConfigError(
                f"AI request failed ({type(exc).__name__}): {safe_message}"
            ) from None


class FakeAIClient:
    """Deterministic AIClient for tests; never touches the network."""

    def __init__(self, response: dict):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def generate_json(self, system_prompt: str, user_payload: dict) -> dict:
        self.calls.append((system_prompt, user_payload))
        return self.response


class FixtureAIClient:
    """Deterministic scenario-backed AIClient for demonstrations."""

    def __init__(self, scenarios: dict[str, dict]):
        self.scenarios = scenarios

    def generate_json(self, system_prompt: str, user_payload: dict) -> dict:
        scenario = user_payload.get("scenario")
        if scenario not in self.scenarios:
            raise AIConfigError(
                f"No fixture response registered for scenario '{scenario}'."
            )
        return self.scenarios[scenario]