"""AI client boundary and defensive JSON parsing for PawPal Sentinel.

This module is the only project module allowed to talk to the Gemini SDK.
It also owns the raw model-response parser used by both the plan critic and
repair agent. Centralizing parsing prevents the two AI components from
silently accepting different output formats.

The Gemini dependency is imported lazily inside ``GeminiAIClient.__init__``.
Importing PawPal+ or Streamlit therefore remains safe when AI configuration
or the optional SDK is unavailable.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from typing import Any, Protocol

DEFAULT_MODEL_NAME = "gemini-3.1-flash-lite"
MAX_MODEL_RESPONSE_CHARS = 100_000
MAX_JSON_DEPTH = 30
MAX_JSON_NODES = 10_000

# Only a complete, surrounding JSON fence is removable. A language other than
# JSON, missing closing fence, or prose around a fence is rejected.
_JSON_FENCE_PATTERN = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```[ \t]*\Z",
    flags=re.IGNORECASE,
)


class AIConfigError(RuntimeError):
    """Raised when the AI client is missing configuration or cannot run safely."""


class AIResponseParseError(ValueError):
    """Raised when raw model output is not one strict JSON object."""


class AIClient(Protocol):
    def generate_json(self, system_prompt: str, user_payload: dict) -> object:
        """Return a raw JSON-like response for strict parsing by the caller."""
        ...


def _redact_secret(text: str, secret: str | None) -> str:
    """Strip a literal secret value from an error before logging or display."""
    if not secret or not text:
        return text
    return text.replace(secret, "[REDACTED]")


def _raise_invalid_constant(value: str) -> None:
    raise AIResponseParseError(
        f"AI response contains non-standard numeric value '{value}'."
    )


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AIResponseParseError(
                f"AI response contains duplicate JSON key '{key}'."
            )
        result[key] = value
    return result


def _validate_json_tree(value: object) -> None:
    """Validate an already-decoded value is bounded, finite, JSON-compatible data."""
    nodes_seen = 0
    total_text_chars = 0
    stack: list[tuple[object, int]] = [(value, 0)]

    while stack:
        current, depth = stack.pop()
        nodes_seen += 1
        if nodes_seen > MAX_JSON_NODES:
            raise AIResponseParseError(
                f"AI response exceeds the maximum of {MAX_JSON_NODES} JSON values."
            )
        if depth > MAX_JSON_DEPTH:
            raise AIResponseParseError(
                f"AI response exceeds the maximum JSON depth of {MAX_JSON_DEPTH}."
            )

        if current is None or isinstance(current, (bool, int)):
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                raise AIResponseParseError(
                    "AI response contains a non-finite numeric value."
                )
            continue
        if isinstance(current, str):
            total_text_chars += len(current)
            if total_text_chars > MAX_MODEL_RESPONSE_CHARS:
                raise AIResponseParseError(
                    "AI response text content exceeds the safe size limit."
                )
            continue
        if isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
            continue
        if isinstance(current, dict):
            for key, child in current.items():
                if not isinstance(key, str):
                    raise AIResponseParseError(
                        "AI response JSON object keys must be strings."
                    )
                total_text_chars += len(key)
                stack.append((child, depth + 1))
            if total_text_chars > MAX_MODEL_RESPONSE_CHARS:
                raise AIResponseParseError(
                    "AI response text content exceeds the safe size limit."
                )
            continue

        raise AIResponseParseError(
            "AI response contains a non-JSON value of type "
            f"{type(current).__name__}."
        )


def parse_model_json_object(raw_response: object) -> dict[str, Any]:
    """Return one strict JSON object from a model response.

    Accepted forms:
    - an already-decoded Python ``dict`` containing only JSON-compatible data;
    - a plain JSON object string;
    - a JSON object wrapped by one complete `````json`` or ````` `` fence.

    Prose before or after JSON, unsupported fence languages, arrays, null,
    duplicate keys, NaN/Infinity, excessive size/depth, and malformed JSON are
    rejected. This function never uses ``eval`` and never attempts to repair
    model text beyond removing a known surrounding fence.
    """
    if isinstance(raw_response, dict):
        _validate_json_tree(raw_response)
        return raw_response

    if not isinstance(raw_response, str):
        raise AIResponseParseError(
            "AI response must be a JSON object or JSON string, got "
            f"{type(raw_response).__name__}."
        )

    text = raw_response.strip()
    if not text:
        raise AIResponseParseError("AI response was empty.")
    if len(text) > MAX_MODEL_RESPONSE_CHARS:
        raise AIResponseParseError(
            f"AI response exceeds the maximum length of {MAX_MODEL_RESPONSE_CHARS} characters."
        )

    if text.startswith("```"):
        match = _JSON_FENCE_PATTERN.fullmatch(text)
        if match is None:
            raise AIResponseParseError(
                "AI response used an unsupported or incomplete code fence."
            )
        text = match.group("body").strip()
        if not text:
            raise AIResponseParseError("AI response JSON fence was empty.")

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_raise_invalid_constant,
        )
    except AIResponseParseError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise AIResponseParseError(
            f"AI response is not valid standalone JSON ({type(exc).__name__})."
        ) from None

    if not isinstance(parsed, dict):
        raise AIResponseParseError(
            f"AI response root must be a JSON object, got {type(parsed).__name__}."
        )

    _validate_json_tree(parsed)
    return parsed


class GeminiAIClient:
    """Production ``AIClient`` backed by the Gemini SDK."""

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
        selected_model = model_name or os.getenv("PAWPAL_MODEL_NAME") or DEFAULT_MODEL_NAME
        if not isinstance(selected_model, str) or not selected_model.strip():
            raise AIConfigError("PAWPAL_MODEL_NAME must be a non-empty string.")
        self.model_name = selected_model.strip()

        try:
            self.client = genai.Client(api_key=api_key)
        except Exception as exc:
            safe_message = _redact_secret(str(exc), api_key)
            raise AIConfigError(
                f"AI client setup failed ({type(exc).__name__}): {safe_message}"
            ) from None

    def generate_json(self, system_prompt: str, user_payload: dict) -> dict[str, Any]:
        """Call Gemini and return one defensively parsed JSON object."""
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt must be a non-empty string.")
        if not isinstance(user_payload, dict):
            raise TypeError("user_payload must be a dictionary.")

        try:
            payload_text = json.dumps(
                user_payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            raise ValueError(
                "user_payload must contain only JSON-serializable finite values."
            ) from None

        # Defense in depth for the explicit secret-handling contract.
        if self._api_key in system_prompt or self._api_key in payload_text:
            raise ValueError("The API key must never be included in an AI prompt payload.")

        prompt = f"{system_prompt.strip()}\n\n{payload_text}"

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            raw_text = getattr(response, "text", None)
        except Exception as exc:
            safe_message = _redact_secret(str(exc), self._api_key)
            raise AIConfigError(
                f"AI request failed ({type(exc).__name__}): {safe_message}"
            ) from None

        # Keep malformed model output distinct from configuration/network errors
        # so the Phase 5 workflow can classify it as INVALID_AI_OUTPUT.
        return parse_model_json_object(raw_text)


class FakeAIClient:
    """Deterministic test client that returns one canned raw response."""

    def __init__(self, response: object):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def generate_json(self, system_prompt: str, user_payload: dict) -> object:
        self.calls.append((system_prompt, user_payload))
        return self.response


class FixtureAIClient:
    """Deterministic scenario-backed client for demonstrations."""

    def __init__(self, scenarios: Mapping[str, object]):
        if not isinstance(scenarios, Mapping):
            raise TypeError("scenarios must be a mapping.")
        self.scenarios = dict(scenarios)
        self.calls: list[tuple[str, dict]] = []

    def generate_json(self, system_prompt: str, user_payload: dict) -> object:
        self.calls.append((system_prompt, user_payload))
        scenario = user_payload.get("scenario")
        if scenario not in self.scenarios:
            raise AIConfigError(
                f"No fixture response registered for scenario '{scenario}'."
            )
        return self.scenarios[scenario]