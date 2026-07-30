"""Static UI-contract tests for the PawPal+ sidebar/Sentinel refactor.

These tests do not import Streamlit or call Gemini. They verify that app.py keeps
private AI internals out of the user interface and preserves the requested
navigation and validated-change display contract.
"""

from __future__ import annotations

import ast
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _source() -> str:
    return APP_PATH.read_text(encoding="utf-8")


def test_app_source_is_valid_python() -> None:
    ast.parse(_source())


def test_sidebar_contains_each_requested_section() -> None:
    source = _source()
    for label in (
        "Owner Setup",
        "Pets",
        "Schedule a Task",
        "Generate Schedule",
        "AI Review",
    ):
        assert f'"{label}"' in source
    assert 'key="pawpal_active_section"' in source


def test_private_sentinel_details_are_not_rendered() -> None:
    source = _source()
    assert "Model-reported confidence" not in source
    assert "Retrieved care-rule sections" not in source
    assert "Sentinel History" not in source
    assert "render_sentinel_history(" not in source


def test_full_draft_is_not_repeated_in_ai_review() -> None:
    source = _source()
    assert "render_draft_plan(" not in source
    assert "render_review_overview(owner, run)" in source


def test_only_validated_moves_are_displayed() -> None:
    source = _source()
    assert "def _validated_changes" in source
    assert 'getattr(validation, "valid", False) is not True' in source
    assert "def _validated_move_changes" in source
    assert "def render_validated_changes" in source
    assert "render_validated_changes(run)" in source


def test_no_change_path_skips_repair_panel() -> None:
    source = _source()
    assert "if status == WorkflowStatus.NO_REPAIR_NEEDED.value" in source
    assert "Intentionally no proposed-repair panel when there are no changes" in source


def test_ai_generation_has_live_progress_feedback() -> None:
    source = _source()
    assert "def render_ai_generation_control" in source
    assert "st.progress(" in source