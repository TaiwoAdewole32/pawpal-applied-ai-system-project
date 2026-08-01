"""Tests for ai_client.py (Phase 4.1 / 4.2)."""

import os
import unittest
from unittest.mock import patch

from ai_client import (
    DEFAULT_MODEL_NAME,
    AIConfigError,
    FakeAIClient,
    GeminiAIClient,
    _redact_secret,
)


class TestFakeAIClient(unittest.TestCase):
    def test_returns_canned_response(self):
        response = {"status": "ok"}
        client = FakeAIClient(response)

        result = client.generate_json("system prompt", {"task_id": "t-1"})

        self.assertEqual(result, response)

    def test_records_calls(self):
        client = FakeAIClient({"status": "ok"})

        client.generate_json("system prompt", {"task_id": "t-1"})

        self.assertEqual(client.calls, [("system prompt", {"task_id": "t-1"})])


class TestGeminiAIClientConfig(unittest.TestCase):
    def test_missing_api_key_raises_ai_config_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AIConfigError):
                GeminiAIClient()

    def test_explicit_api_key_constructs_without_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("google.genai.Client") as mock_client:
                client = GeminiAIClient(api_key="fake-key-123")

        self.assertEqual(client.model_name, DEFAULT_MODEL_NAME)
        mock_client.assert_called_once_with(api_key="fake-key-123")

    def test_env_api_key_constructs_without_error(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key-456"}, clear=True):
            with patch("google.genai.Client"):
                GeminiAIClient()  # must not raise


class TestSecretRedaction(unittest.TestCase):
    def test_redacts_secret_from_message(self):
        message = "request failed, header Authorization: Bearer fake-key-123"

        redacted = _redact_secret(message, "fake-key-123")

        self.assertNotIn("fake-key-123", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_generate_json_error_does_not_leak_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("google.genai.Client") as mock_client_cls:
                mock_client = mock_client_cls.return_value
                mock_client.models.generate_content.side_effect = RuntimeError(
                    "auth header leaked: fake-key-789"
                )
                client = GeminiAIClient(api_key="fake-key-789")

                with self.assertRaises(AIConfigError) as ctx:
                    client.generate_json("system prompt", {"task_id": "t-1"})

        self.assertNotIn("fake-key-789", str(ctx.exception))


class TestImportSafety(unittest.TestCase):
    def test_import_does_not_require_api_key(self):
        # Load a throwaway copy of the module (rather than
        # importlib.reload(ai_client), which mutates sys.modules["ai_client"]
        # in place and would replace classes like AIResponseParseError with
        # new objects of the same name -- silently breaking isinstance/
        # pytest.raises checks in every other test module that already
        # imported the original classes).
        import importlib.util

        import ai_client

        with patch.dict(os.environ, {}, clear=True):
            spec = importlib.util.spec_from_file_location(
                "ai_client_import_safety_check", ai_client.__file__
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # must not raise


if __name__ == "__main__":
    unittest.main()
