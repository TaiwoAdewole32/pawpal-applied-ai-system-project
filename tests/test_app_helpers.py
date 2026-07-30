"""Unit tests for the pure display-logic helpers in app.py (Phase 6 UI).

app.py is a Streamlit script, but the functions covered here
(``_guardrail_summary``, ``_bounded_display_text``, ``_has_validated_move``,
``_current_schedule_version_matches``) contain no widget rendering — they are
plain data transforms that Streamlit's "bare mode" (running outside
``streamlit run``) lets us import and call directly, the same way the rest of
this project unit-tests deterministic helpers (see test_validator.py,
test_sentinel_models.py). Streamlit's own bare-mode warnings on import are
expected and harmless.

These tests intentionally do not touch any ``st.*`` rendering call directly;
they test only the logic that decides *what* would be rendered, per Phase
6.9's guidance that automated tests should focus on logic, not widgets.
"""

from __future__ import annotations

import datetime
import types

import pytest
import streamlit as st

import app
from pawpal_system import Owner, Pet, Priority, Task
from sentinel_models import ProposedChange, build_schedule_snapshot


# ---------------------------------------------------------------------------
# _guardrail_summary
# ---------------------------------------------------------------------------


def _all_check_names() -> list[str]:
    return [name for name, _label in app._VALIDATOR_CHECK_LABELS]


def test_guardrail_summary_all_pass():
    checks = {name: True for name in _all_check_names()}
    passed, total, failing = app._guardrail_summary(checks)
    assert passed == total == len(checks)
    assert failing == []


def test_guardrail_summary_all_fail():
    names = _all_check_names()
    checks = {name: False for name in names}
    passed, total, failing = app._guardrail_summary(checks)
    assert passed == 0
    assert total == len(names)
    assert len(failing) == len(names)


def test_guardrail_summary_mixed_pass_and_fail():
    names = _all_check_names()
    checks = {name: (i % 2 == 0) for i, name in enumerate(names)}
    passed, total, failing = app._guardrail_summary(checks)
    expected_passed = sum(1 for value in checks.values() if value is True)
    assert passed == expected_passed
    assert total == len(names)
    assert len(failing) == len(names) - expected_passed


def test_guardrail_summary_empty_dict():
    assert app._guardrail_summary({}) == (0, 0, [])


@pytest.mark.parametrize("bad_input", [None, [], "checks", 42, True, ()])
def test_guardrail_summary_non_dict_input_is_safe(bad_input):
    assert app._guardrail_summary(bad_input) == (0, 0, [])


def test_guardrail_summary_unknown_key_counts_and_labels_it():
    checks = {"schema_valid": True, "some_future_check": True}
    passed, total, failing = app._guardrail_summary(checks)
    assert total == 2
    assert passed == 2
    assert failing == []

    checks_failing = {"schema_valid": True, "some_future_check": False}
    passed, total, failing = app._guardrail_summary(checks_failing)
    assert total == 2
    assert passed == 1
    assert failing == ["Some future check"]


@pytest.mark.parametrize("truthy_non_bool", [1, "true", "True", 1.0])
def test_guardrail_summary_non_bool_truthy_value_counts_as_failing(truthy_non_bool):
    checks = {"schema_valid": truthy_non_bool}
    passed, total, failing = app._guardrail_summary(checks)
    assert passed == 0
    assert total == 1
    assert len(failing) == 1


def test_guardrail_summary_none_value_counts_as_failing():
    checks = {"schema_valid": None}
    passed, total, failing = app._guardrail_summary(checks)
    assert passed == 0
    assert failing


def test_guardrail_summary_matches_full_checklist_count():
    # Sanity: summarizing the exact named-check set matches its own length,
    # so the short summary and the full expander checklist can never disagree.
    checks = {name: True for name in _all_check_names()}
    _passed, total, _failing = app._guardrail_summary(checks)
    assert total == len(app._VALIDATOR_CHECK_LABELS)


# ---------------------------------------------------------------------------
# _bounded_display_text
# ---------------------------------------------------------------------------


def test_bounded_display_text_normal_text():
    assert app._bounded_display_text("  hello  ") == "hello"


@pytest.mark.parametrize("bad_value", [None, 123, [], {}, True])
def test_bounded_display_text_non_string_uses_default(bad_value):
    assert app._bounded_display_text(bad_value, "fallback") == "fallback"


def test_bounded_display_text_empty_and_whitespace_use_default():
    assert app._bounded_display_text("", "fallback") == "fallback"
    assert app._bounded_display_text("   ", "fallback") == "fallback"


def test_bounded_display_text_exactly_at_max_chars_is_unchanged():
    text = "1234567890"
    assert app._bounded_display_text(text, max_chars=10) == text


def test_bounded_display_text_one_over_max_chars_is_truncated():
    text = "12345678901"
    result = app._bounded_display_text(text, max_chars=10)
    assert result == "1234567890..."


@pytest.mark.parametrize("bad_max_chars", [0, -1, -100, "10", None, True])
def test_bounded_display_text_invalid_max_chars_falls_back_to_default_cap(bad_max_chars):
    # An invalid max_chars must not crash or silently allow unbounded text;
    # it falls back to the function's own 500-char default cap.
    result = app._bounded_display_text("short text", max_chars=bad_max_chars)
    assert result == "short text"


# ---------------------------------------------------------------------------
# _has_validated_move
# ---------------------------------------------------------------------------


def _change(action: str) -> ProposedChange:
    return ProposedChange(
        task_id="task-1",
        action=action,
        original_time="08:00",
        new_time="09:00" if action == "move" else None,
        reason="test",
    )


def test_has_validated_move_empty_tuple_is_false():
    run = types.SimpleNamespace(validated_changes=())
    assert app._has_validated_move(run) is False


def test_has_validated_move_only_non_move_actions_is_false():
    run = types.SimpleNamespace(
        validated_changes=(_change("keep"), _change("defer_for_review"))
    )
    assert app._has_validated_move(run) is False


def test_has_validated_move_one_move_among_others_is_true():
    run = types.SimpleNamespace(
        validated_changes=(_change("keep"), _change("move"), _change("defer_for_review"))
    )
    assert app._has_validated_move(run) is True


@pytest.mark.parametrize("bad_changes", [None, "move", 42, {"a": 1}, {_change("move")}])
def test_has_validated_move_non_sequence_is_false(bad_changes):
    run = types.SimpleNamespace(validated_changes=bad_changes)
    assert app._has_validated_move(run) is False


def test_has_validated_move_missing_attribute_is_false():
    run = types.SimpleNamespace()
    assert app._has_validated_move(run) is False


# ---------------------------------------------------------------------------
# _current_schedule_version_matches
# ---------------------------------------------------------------------------


def _make_owner_with_one_task() -> Owner:
    owner = Owner(
        name="TestOwner",
        startTime=datetime.time(7, 0),
        endTime=datetime.time(19, 0),
        preferences={},
    )
    pet = Pet(
        name="Fixture",
        species="Dog",
        breed="",
        age=3,
        foodType="Dry",
        medication="none",
        energyLevel=5,
    )
    owner.addPet(pet)
    owner.scheduler.addTask(
        Task(
            taskName="Walk",
            taskType="walk",
            durationMinutes=20,
            priority=Priority.LOW,
            pet=pet,
            preferredTime=datetime.time(8, 0),
        )
    )
    return owner


def test_current_schedule_version_matches_when_versions_align():
    owner = _make_owner_with_one_task()
    snapshot = build_schedule_snapshot(owner)
    st.session_state[app.SENTINEL_VERSION_KEY] = snapshot.version
    run = types.SimpleNamespace(snapshot=types.SimpleNamespace(version=snapshot.version))

    matches, message = app._current_schedule_version_matches(owner, run)
    assert matches is True
    assert "matches" in message.lower()


def test_current_schedule_version_matches_false_on_mismatch():
    owner = _make_owner_with_one_task()
    st.session_state[app.SENTINEL_VERSION_KEY] = "stored-version-hash"
    run = types.SimpleNamespace(snapshot=types.SimpleNamespace(version="different-version-hash"))

    matches, message = app._current_schedule_version_matches(owner, run)
    assert matches is False
    assert message


def test_current_schedule_version_matches_false_when_stored_version_missing():
    owner = _make_owner_with_one_task()
    st.session_state[app.SENTINEL_VERSION_KEY] = None
    run = types.SimpleNamespace(snapshot=types.SimpleNamespace(version="some-version"))

    matches, message = app._current_schedule_version_matches(owner, run)
    assert matches is False
    assert "stored" in message.lower()


def test_current_schedule_version_matches_false_when_reviewed_version_missing():
    owner = _make_owner_with_one_task()
    st.session_state[app.SENTINEL_VERSION_KEY] = "stored-version-hash"
    run = types.SimpleNamespace(snapshot=types.SimpleNamespace(version=""))

    matches, message = app._current_schedule_version_matches(owner, run)
    assert matches is False
    assert "reviewed" in message.lower()


@pytest.mark.parametrize("bad_owner", [None, "not-an-owner", 42, object()])
def test_current_schedule_version_matches_false_for_non_owner(bad_owner):
    run = types.SimpleNamespace(snapshot=types.SimpleNamespace(version="some-version"))
    matches, message = app._current_schedule_version_matches(bad_owner, run)
    assert matches is False
    assert "owner" in message.lower()


def test_current_schedule_version_matches_false_when_rebuild_raises(monkeypatch):
    owner = _make_owner_with_one_task()
    st.session_state[app.SENTINEL_VERSION_KEY] = "stored-version-hash"
    run = types.SimpleNamespace(snapshot=types.SimpleNamespace(version="stored-version-hash"))

    def _raise(_owner):
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "build_schedule_snapshot", _raise)

    matches, message = app._current_schedule_version_matches(owner, run)
    assert matches is False
    assert isinstance(message, str) and message
