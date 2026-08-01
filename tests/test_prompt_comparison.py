from __future__ import annotations

import json
from datetime import datetime, timezone

from evaluate import DEFAULT_SCENARIOS_PATH, _load_scenario_file
from prompt_comparison import (
    _COMPARISON_SCENARIO_IDS,
    _measure,
    print_comparison,
    run_fixture_comparison,
    run_live_comparison,
    write_markdown_table,
)
from sentinel_models import ScheduleSnapshot, TaskSnapshot


def fixed_clock() -> datetime:
    return datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _snapshot() -> ScheduleSnapshot:
    tasks = (
        TaskSnapshot(
            task_id="med-1",
            task_name="Medication",
            task_type="medication",
            duration_minutes=10,
            priority="high",
            pet_id="pet-1",
            pet_name="Fixture",
            preferred_time="08:00",
            recurrence="none",
            due_date="2026-07-30",
            flexibility="fixed",
        ),
        TaskSnapshot(
            task_id="walk-1",
            task_name="Walk",
            task_type="walk",
            duration_minutes=30,
            priority="medium",
            pet_id="pet-1",
            pet_name="Fixture",
            preferred_time="08:00",
            recurrence="none",
            due_date="2026-07-30",
            flexibility="flexible",
        ),
    )
    return ScheduleSnapshot(
        owner_name="Fixture",
        availability_start="07:00",
        availability_end="19:00",
        tasks=tasks,
        unscheduled_task_ids=(),
        version="snapshot-v1",
    )


def test_run_fixture_comparison_covers_all_five_scenarios():
    report = run_fixture_comparison(clock=fixed_clock)
    assert report["ok"] is True
    assert report["scenario_ids"] == list(_COMPARISON_SCENARIO_IDS)
    assert len(report["per_scenario"]) == 5
    for row in report["per_scenario"]:
        assert "baseline" in row and "specialized" in row


def test_specialized_condition_beats_baseline_on_key_guardrails():
    report = run_fixture_comparison(clock=fixed_clock)
    summary = report["summary"]

    baseline_unsafe = summary["baseline"]["unsafe_proposals"]
    specialized_unsafe = summary["specialized"]["unsafe_proposals"]
    assert specialized_unsafe[0] < baseline_unsafe[0]
    assert specialized_unsafe[0] == 0

    baseline_fixed = summary["baseline"]["fixed_tasks_preserved"]
    specialized_fixed = summary["specialized"]["fixed_tasks_preserved"]
    assert specialized_fixed[0] > baseline_fixed[0]
    assert specialized_fixed[0] == specialized_fixed[1]

    baseline_conflict_free = summary["baseline"]["conflict_free_accepted"]
    specialized_conflict_free = summary["specialized"]["conflict_free_accepted"]
    assert specialized_conflict_free[0] > baseline_conflict_free[0]


def test_measure_never_raises_on_malformed_raw_responses():
    snapshot = _snapshot()

    for malformed in (
        None,
        "not a dict at all",
        123,
        [],
        {"summary": "missing proposed_changes"},
        {"proposed_changes": "not a list", "summary": "wrong type"},
        {"proposed_changes": None, "summary": "null changes"},
    ):
        result = _measure(snapshot, malformed)
        assert result["structured_output_valid"] is False
        assert result["validator_valid"] is False
        assert isinstance(result["checks"], dict)


def test_measure_accepts_a_genuinely_valid_proposal():
    snapshot = _snapshot()
    valid_response = {
        "proposed_changes": [
            {
                "task_id": "walk-1",
                "action": "move",
                "original_time": "08:00",
                "new_time": "09:00",
                "reason": "Move the flexible walk.",
            }
        ],
        "summary": "Move only the flexible walk.",
    }
    result = _measure(snapshot, valid_response)
    assert result["structured_output_valid"] is True
    assert result["validator_valid"] is True
    assert result["fixed_tasks_preserved"] is True
    assert result["conflict_free_accepted"] is True


def test_report_json_round_trips(tmp_path):
    report = run_fixture_comparison(clock=fixed_clock)
    report_path = tmp_path / "prompt_comparison.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    with report_path.open("r", encoding="utf-8") as stream:
        loaded = json.load(stream)
    assert loaded["mode"] == "fixture"


def test_markdown_table_contains_all_five_metric_rows(tmp_path):
    report = run_fixture_comparison(clock=fixed_clock)
    md_path = tmp_path / "prompt_comparison.md"
    write_markdown_table(report, str(md_path))

    content = md_path.read_text(encoding="utf-8")
    for label in (
        "Valid structured outputs",
        "Fixed tasks preserved",
        "Unsafe proposals",
        "Unknown task IDs",
        "Conflict-free accepted plans",
    ):
        assert label in content
    assert "| Metric | Baseline | Specialized |" in content


def test_live_comparison_without_api_key_fails_gracefully(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    report = run_live_comparison(api_key=None, clock=fixed_clock)
    assert report["ok"] is False
    assert "GEMINI_API_KEY" in report["message"]


def test_print_comparison_never_raises_on_any_report_shape(monkeypatch, capsys):
    """print_comparison() is what a Streamlit page would call to render this
    evidence, so every report shape the module can actually produce must be
    safe to print -- including both graceful-failure shapes and a rare
    hand-built live success with no scored scenarios at all.
    """
    fixture_report = run_fixture_comparison(clock=fixed_clock)
    print_comparison(fixture_report)
    assert capsys.readouterr().out.strip()

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    live_failure = run_live_comparison(api_key=None, clock=fixed_clock)
    print_comparison(live_failure)
    assert capsys.readouterr().out.strip()

    empty_live_success = {
        "ok": True,
        "mode": "live",
        "model_name": "gemini-test-model",
        "baseline_prompt": "Review this pet-care schedule and improve it.",
        "specialized_prompt_version": "pawpal-repair-v2-few-shot",
        "scenario_ids": [],
        "per_scenario": [],
        "summary": {},
        "note": "",
    }
    print_comparison(empty_live_success)
    assert capsys.readouterr().out.strip()


def test_run_fixture_comparison_missing_scenario_id_fails_gracefully(tmp_path):
    """Points run_fixture_comparison() at a scenarios file that's missing one
    of the five required comparison scenario ids and confirms a graceful
    failure dict rather than a KeyError.
    """
    all_scenarios = _load_scenario_file(DEFAULT_SCENARIOS_PATH)
    missing_id = "capacity_exceeds_availability_window"
    trimmed = [s for s in all_scenarios if s.get("id") != missing_id]
    assert len(trimmed) == len(all_scenarios) - 1

    scenario_file = tmp_path / "trimmed_scenarios.json"
    scenario_file.write_text(json.dumps({"scenarios": trimmed}), encoding="utf-8")

    report = run_fixture_comparison(scenarios_path=str(scenario_file), clock=fixed_clock)

    assert report["ok"] is False
    assert missing_id in report["message"]
