"""AI client boundary for PawPal Sentinel (Phase 4.1 / 4.2).

This module is the only place in the project allowed to talk to the Gemini
SDK. `plan_critic.py` / `repair_agent.py` (Phase 4.4+) call `generate_json`
with a `system_prompt` and a `user_payload`; `app.py` never calls the SDK
directly.

Data-minimization rule (see PAWPAL_SENTINEL_IMPLEMENTATION_PLAN.md Phase 4.1):
`user_payload` must be built only from `ScheduleSnapshot`/`TaskSnapshot`
fields. This module does not enforce that shape itself — it is enforced by
what callers choose to pass — but no method here ever adds anything to the
payload beyond what the caller supplies.

Secret-handling rule: the API key must never appear in a prompt payload, and
must be stripped from any exception message before it reaches a log or the
UI, since some HTTP client libraries echo request headers in error text.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

from google import genai

DEFAULT_MODEL_NAME = "gemini-3.1-flash-lite"


class AIConfigError(RuntimeError):
    """Raised when the AI client is missing required configuration."""


class AIClient(Protocol):
    def generate_json(self, system_prompt: str, user_payload: dict) -> dict:
        ...


def _redact_secret(text: str, secret: str | None) -> str:
    """Strip a literal secret value out of an error message before it is logged or shown."""
    if not secret or not text:
        return text
    return text.replace(secret, "[REDACTED]")


class GeminiAIClient:
    """Production `AIClient` backed by the Gemini SDK.

    Construction reads `GEMINI_API_KEY` / `PAWPAL_MODEL_NAME` from the
    environment when not passed explicitly, and raises `AIConfigError`
    immediately if no key is available. Nothing here runs at import time,
    so importing this module never crashes even with no configuration.
    """

    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise AIConfigError(
                "Missing GEMINI_API_KEY environment variable. "
                "Set it in your shell or .env file to enable AI review features."
            )

        self._api_key = api_key
        self.model_name = model_name or os.getenv("PAWPAL_MODEL_NAME") or DEFAULT_MODEL_NAME
        self.client = genai.Client(api_key=api_key)

    def generate_json(self, system_prompt: str, user_payload: dict) -> dict:
        """Call Gemini with `system_prompt` plus a JSON-encoded `user_payload` and parse the reply as JSON."""
        prompt = f"{system_prompt}\n\n{json.dumps(user_payload)}"

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            text = (response.text or "").strip()
            return json.loads(text)
        except Exception as exc:
            safe_message = _redact_secret(str(exc), self._api_key)
            raise AIConfigError(
                f"AI request failed ({type(exc).__name__}): {safe_message}"
            ) from None


class FakeAIClient:
    """Deterministic `AIClient` for tests. Returns a canned response, never touches the network."""

    def __init__(self, response: dict):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def generate_json(self, system_prompt: str, user_payload: dict) -> dict:
        self.calls.append((system_prompt, user_payload))
        return self.response


class FixtureAIClient:
    """Deterministic `AIClient` for demonstrations, backed by a fixed set of canned scenario responses.

    Not a live AI result — callers must not present its output as one.
    """

    def __init__(self, scenarios: dict[str, dict]):
        self.scenarios = scenarios

    def generate_json(self, system_prompt: str, user_payload: dict) -> dict:
        scenario = user_payload.get("scenario")
        if scenario not in self.scenarios:
            raise AIConfigError(
                f"No fixture response registered for scenario '{scenario}'."
            )
        return self.scenarios[scenario]
