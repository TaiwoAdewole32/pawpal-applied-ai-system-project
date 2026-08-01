from __future__ import annotations

import json
from datetime import datetime, timezone

from evaluate import (
    DEFAULT_SCENARIOS_PATH,
    _load_scenario_file,
    _require_scenario_shape,
    print_summary,
    run_fixture_mode,
    run_live_mode,
)


def fixed_clock() -> datetime:
    return datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _base_scenario(**overrides) -> dict:
    scenario = {
        "id": "good_scenario",
        "description": "A minimal conflict-free scenario used for harness tests.",
        "owner": {"name": "TestOwner", "startTime": "08:00", "endTime": "16:00", "preferences": {}},
        "pets": [
            {"petId": "pet-1", "name": "Fixture", "species": "Dog", "breed": "Mix", "age": 3,
             "foodType": "Kibble", "medication": "none", "energyLevel": 5, "careNeeds": []}
        ],
        "tasks": [
            {"taskId": "feed-1", "taskName": "Feeding", "taskType": "feeding",
             "durationMinutes": 15, "priority": "medium", "petId": "pet-1",
             "preferredTime": "09:00", "recurrence": "none", "dueDate": "today",
             "completed": False, "notes": "", "flexibility": "preferred"}
        ],
        "ai_responses": {
            "critic": {
                "status": "no_change_needed",
                "summary": "No supported schedule issue was found.",
                "issues": [],
                "confidence": 0.9,
            },
            "repair": [{"proposed_changes": [], "summary": "No changes are needed."}],
        },
        "expected": {"workflow_status": "no_repair_needed", "checks": {}},
    }
    scenario.update(overrides)
    return scenario


def test_scenario_file_loads_and_has_at_least_ten_scenarios():
    scenarios = _load_scenario_file(DEFAULT_SCENARIOS_PATH)
    assert len(scenarios) >= 10
    for scenario in scenarios:
        _require_scenario_shape(scenario)


def test_missing_required_field_is_reported_without_stopping_the_run(tmp_path):
    bad_scenario = _base_scenario(id="bad_scenario")
    del bad_scenario["expected"]
    good_scenario = _base_scenario(id="good_scenario_2")

    scenario_file = tmp_path / "scenarios.json"
    scenario_file.write_text(
        json.dumps({"scenarios": [bad_scenario, good_scenario]}), encoding="utf-8"
    )
    report_path = tmp_path / "report.json"

    report = run_fixture_mode(
        scenarios_path=str(scenario_file), report_path=str(report_path), clock=fixed_clock
    )

    assert report["scenarios_tested"] == 2
    assert report["scenario_errors"] == 1
    records_by_id = {record["id"]: record for record in report["records"]}
    assert records_by_id["bad_scenario"]["error"] is not None
    assert records_by_id["good_scenario_2"]["error"] is None
    assert records_by_id["good_scenario_2"]["workflow_status_match"] is True


def test_unknown_expected_status_is_rejected_as_a_scenario_error(tmp_path):
    bad_scenario = _base_scenario(id="unknown_status_scenario")
    bad_scenario["expected"] = {"workflow_status": "not_a_real_status", "checks": {}}

    scenario_file = tmp_path / "scenarios.json"
    scenario_file.write_text(json.dumps({"scenarios": [bad_scenario]}), encoding="utf-8")
    report_path = tmp_path / "report.json"

    report = run_fixture_mode(
        scenarios_path=str(scenario_file), report_path=str(report_path), clock=fixed_clock
    )

    assert report["scenario_errors"] == 1
    assert "not a real WorkflowStatus" in report["records"][0]["error"]


def test_fixture_mode_is_deterministic_across_runs(tmp_path):
    report_path_a = tmp_path / "report_a.json"
    report_path_b = tmp_path / "report_b.json"

    report_a = run_fixture_mode(report_path=str(report_path_a), clock=fixed_clock)
    report_b = run_fixture_mode(report_path=str(report_path_b), clock=fixed_clock)

    assert report_a == report_b


def test_empty_scenario_list_avoids_division_by_zero(tmp_path):
    scenario_file = tmp_path / "empty.json"
    scenario_file.write_text(json.dumps({"scenarios": []}), encoding="utf-8")
    report_path = tmp_path / "report.json"

    report = run_fixture_mode(
        scenarios_path=str(scenario_file), report_path=str(report_path), clock=fixed_clock
    )

    assert report["scenarios_tested"] == 0
    assert report["overall_checks_total"] == 0
    assert report["overall_checks_passed"] == 0
    assert report["reliability_rate"] == 0.0


def test_report_file_written_is_valid_json(tmp_path):
    report_path = tmp_path / "report.json"
    run_fixture_mode(report_path=str(report_path), clock=fixed_clock)

    with report_path.open("r", encoding="utf-8") as stream:
        loaded = json.load(stream)
    assert loaded["mode"] == "fixture"


def test_live_mode_without_api_key_fails_gracefully(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    report_path = tmp_path / "live.json"

    report = run_live_mode(report_path=str(report_path), api_key=None, clock=fixed_clock)

    assert report["ok"] is False
    assert "GEMINI_API_KEY" in report["message"]
    assert not report_path.exists()


def test_medication_conflict_scenario_reaches_awaiting_owner_approval(tmp_path):
    report_path = tmp_path / "report.json"
    report = run_fixture_mode(report_path=str(report_path), clock=fixed_clock)

    records_by_id = {record["id"]: record for record in report["records"]}
    record = records_by_id["medication_conflict_with_flexible_walk"]
    assert record["error"] is None
    assert record["actual_workflow_status"] == "awaiting_owner_approval"
    assert record["workflow_status_match"] is True
    assert record["mutation_safe"] is True


def test_stale_proposal_scenario_reaches_stale_approval(tmp_path):
    report_path = tmp_path / "report.json"
    report = run_fixture_mode(report_path=str(report_path), clock=fixed_clock)

    records_by_id = {record["id"]: record for record in report["records"]}
    record = records_by_id["stale_proposal_before_approval"]
    assert record["error"] is None
    assert record["actual_approval_status"] == "stale_proposal"
    assert record["approval_status_match"] is True


def test_notes_prompt_injection_never_reaches_ai_payload(tmp_path):
    report_path = tmp_path / "report.json"
    report = run_fixture_mode(report_path=str(report_path), clock=fixed_clock)

    records_by_id = {record["id"]: record for record in report["records"]}
    record = records_by_id["task_notes_prompt_injection"]
    assert record["error"] is None
    assert record["notes_excluded_from_ai_payload"] is True


def test_all_scenarios_run_without_harness_errors(tmp_path):
    report_path = tmp_path / "report.json"
    report = run_fixture_mode(report_path=str(report_path), clock=fixed_clock)
    assert report["scenario_errors"] == 0


# ---------------------------------------------------------------------------
# Phase 7.3: named reliability metrics
# ---------------------------------------------------------------------------


def test_critic_structured_valid_is_false_only_for_the_malformed_output_scenario(tmp_path):
    report_path = tmp_path / "report.json"
    report = run_fixture_mode(report_path=str(report_path), clock=fixed_clock)

    records_by_id = {record["id"]: record for record in report["records"]}
    for scenario_id, record in records_by_id.items():
        expected_valid = scenario_id != "ai_returns_malformed_output"
        assert record["critic_structured_valid"] is expected_valid, scenario_id


def test_repair_structured_valid_is_false_only_for_the_unknown_task_id_scenario(tmp_path):
    report_path = tmp_path / "report.json"
    report = run_fixture_mode(report_path=str(report_path), clock=fixed_clock)

    records_by_id = {record["id"]: record for record in report["records"]}
    for scenario_id, record in records_by_id.items():
        expected_valid = scenario_id != "repair_proposes_unknown_task_id"
        assert record["repair_structured_valid"] is expected_valid, scenario_id


def test_task_selection_correct_for_scenarios_with_expected_moved_task_ids(tmp_path):
    report_path = tmp_path / "report.json"
    report = run_fixture_mode(report_path=str(report_path), clock=fixed_clock)

    records_by_id = {record["id"]: record for record in report["records"]}
    record = records_by_id["medication_conflict_with_flexible_walk"]
    assert record["moved_task_ids"] == ["walk-p1"]
    assert record["task_selection_correct"] is True
    assert report["task_selection_evaluated"] == report["task_selection_correct_count"]
    assert report["task_selection_evaluated"] >= 5


def test_aggregate_guardrail_metrics_are_populated_and_shaped_correctly(tmp_path):
    report_path = tmp_path / "report.json"
    report = run_fixture_mode(report_path=str(report_path), clock=fixed_clock)

    for key in (
        "fixed_task_preservation",
        "medication_task_preservation",
        "unknown_task_rejection",
        "availability_compliance",
        "conflict_free_accepted",
        "unsafe_proposal_rejection",
    ):
        passed, total = report[key]
        assert total > 0, key
        assert 0 <= passed <= total, key

    # Scenarios 3, 5, 6, and 11 each have one deliberately-invalid first
    # attempt, so the pooled attempt-level guardrails must catch at least one
    # real failure -- a suite that is 100% green everywhere would mean the
    # metrics aren't actually looking at the invalid attempts.
    assert report["fixed_task_preservation"][0] < report["fixed_task_preservation"][1]
    assert report["availability_compliance"][0] < report["availability_compliance"][1]
    assert report["conflict_free_accepted"][0] < report["conflict_free_accepted"][1]


def test_issue_detection_metric_excludes_scenarios_without_a_critic_result(tmp_path):
    report_path = tmp_path / "report.json"
    report = run_fixture_mode(report_path=str(report_path), clock=fixed_clock)

    records_by_id = {record["id"]: record for record in report["records"]}
    # The malformed-output scenario never produced a typed CriticResult.
    assert records_by_id["ai_returns_malformed_output"]["issue_detection_correct"] is None
    assert report["issue_detection_evaluated"] == report["issue_detection_correct_count"]


# ---------------------------------------------------------------------------
# Phase 7.5: evaluation harness tests
# ---------------------------------------------------------------------------


def test_summary_counts_match_detailed_records(tmp_path):
    """Independently recompute every aggregate from report['records'] (not by
    importing evaluate.py's own aggregation helpers) and cross-check it against
    the report's top-level summary -- the specific Phase 7.5 requirement that
    'summary counts match detailed records'.
    """
    report_path = tmp_path / "report.json"
    report = run_fixture_mode(report_path=str(report_path), clock=fixed_clock)

    executed = [r for r in report["records"] if r.get("error") is None]
    assert report["executed_scenarios"] == len(executed)

    assert report["workflow_status_matches"] == sum(1 for r in executed if r["workflow_status_match"])
    assert report["mutation_safe_count"] == sum(1 for r in executed if r["mutation_safe"])
    assert report["validator_checks_evaluated"] == sum(len(r["checks_match"]) for r in executed)
    assert report["validator_checks_passed"] == sum(sum(r["checks_match"].values()) for r in executed)

    approval_records = [r for r in executed if "approval_status_match" in r]
    assert report["approval_stage_scenarios"] == len(approval_records)
    assert report["approval_stage_matches"] == sum(
        1 for r in approval_records if r["approval_status_match"]
    )

    injection_records = [r for r in executed if "notes_excluded_from_ai_payload" in r]
    assert report["injection_check_scenarios"] == len(injection_records)
    assert report["injection_check_passed"] == sum(
        1 for r in injection_records if r["notes_excluded_from_ai_payload"]
    )

    assert report["critic_structured_valid_count"] == sum(
        1 for r in executed if r["critic_structured_valid"]
    )
    assert report["repair_structured_valid_count"] == sum(
        1 for r in executed if r["repair_structured_valid"]
    )

    issue_records = [r for r in executed if r.get("issue_detection_correct") is not None]
    assert report["issue_detection_evaluated"] == len(issue_records)
    assert report["issue_detection_correct_count"] == sum(
        1 for r in issue_records if r["issue_detection_correct"]
    )

    task_selection_records = [r for r in executed if "task_selection_correct" in r]
    assert report["task_selection_evaluated"] == len(task_selection_records)
    assert report["task_selection_correct_count"] == sum(
        1 for r in task_selection_records if r["task_selection_correct"]
    )

    all_attempts = [attempt for r in executed for attempt in r["attempt_checks"]]

    def _recompute(predicate):
        total = len(all_attempts)
        passed = sum(1 for attempt in all_attempts if predicate(attempt))
        return (passed, total)

    assert report["fixed_task_preservation"] == _recompute(
        lambda a: a["checks"].get("fixed_tasks_unchanged") is True
    )
    assert report["medication_task_preservation"] == _recompute(
        lambda a: a["checks"].get("medication_tasks_unchanged") is True
    )
    assert report["unknown_task_rejection"] == _recompute(
        lambda a: a["checks"].get("task_ids_known") is True
    )
    assert report["availability_compliance"] == _recompute(
        lambda a: a["checks"].get("inside_availability") is True
    )
    assert report["conflict_free_accepted"] == _recompute(
        lambda a: bool(a["valid"]) and a["checks"].get("no_new_conflicts") is True
    )

    unsafe_count = sum(1 for a in all_attempts if not a["valid"])
    assert report["unsafe_proposal_rejection"] == (unsafe_count, unsafe_count)

    # Independently recomputed pooled reliability flags -- a fresh
    # reimplementation of the counting rule (not a call into evaluate.py's own
    # _flatten_checks), so this genuinely cannot pass "by construction".
    def _flags_for(record):
        flags = [bool(record["workflow_status_match"])]
        flags.extend(bool(v) for v in record["checks_match"].values())
        flags.append(bool(record["mutation_safe"]))
        flags.append(bool(record["critic_structured_valid"]))
        flags.append(bool(record["repair_structured_valid"]))
        if record.get("issue_detection_correct") is not None:
            flags.append(bool(record["issue_detection_correct"]))
        if "task_selection_correct" in record:
            flags.append(bool(record["task_selection_correct"]))
        if "approval_status_match" in record:
            flags.append(bool(record["approval_status_match"]))
        if "notes_excluded_from_ai_payload" in record:
            flags.append(bool(record["notes_excluded_from_ai_payload"]))
        for attempt in record["attempt_checks"]:
            checks = attempt["checks"]
            flags.append(checks.get("fixed_tasks_unchanged") is True)
            flags.append(checks.get("medication_tasks_unchanged") is True)
            flags.append(checks.get("task_ids_known") is True)
            flags.append(checks.get("inside_availability") is True)
            if attempt["valid"]:
                flags.append(checks.get("no_new_conflicts") is True)
        return flags

    all_flags = [flag for r in executed for flag in _flags_for(r)]
    assert report["overall_checks_total"] == len(all_flags)
    assert report["overall_checks_passed"] == sum(1 for flag in all_flags if flag)


def test_print_summary_never_raises_on_any_report_shape(tmp_path, monkeypatch, capsys):
    """print_summary() is what a Streamlit page would call to render this
    evidence, so every report shape evaluate.py can actually produce must be
    safe to print -- including both graceful-failure shapes.
    """
    fixture_report = run_fixture_mode(report_path=str(tmp_path / "r1.json"), clock=fixed_clock)
    print_summary(fixture_report)
    assert capsys.readouterr().out.strip()

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    live_failure = run_live_mode(report_path=str(tmp_path / "r2.json"), api_key=None, clock=fixed_clock)
    print_summary(live_failure)
    assert capsys.readouterr().out.strip()

    # Rare-but-legal hand-built shapes not produced by the two calls above.
    empty_live_success = {
        "ok": True,
        "mode": "live",
        "model_name": "gemini-test-model",
        "scenarios_run": 0,
        "results": [],
        "note": "",
    }
    print_summary(empty_live_success)
    assert capsys.readouterr().out.strip()

    corrupt_fixture_failure = {
        "ok": False,
        "mode": "fixture",
        "message": "Scenario file 'ghost.json' is not valid JSON.",
        "scenarios_tested": 0,
        "records": [],
    }
    print_summary(corrupt_fixture_failure)
    assert capsys.readouterr().out.strip()


def test_malformed_json_scenario_file_fails_gracefully(tmp_path):
    """Literally invalid JSON syntax (not just a semantically-bad scenario)
    must be reported, not raised, out of run_fixture_mode().
    """
    scenario_file = tmp_path / "corrupt.json"
    scenario_file.write_text('{"scenarios": [ this is not valid json', encoding="utf-8")
    report_path = tmp_path / "report.json"

    report = run_fixture_mode(
        scenarios_path=str(scenario_file), report_path=str(report_path), clock=fixed_clock
    )

    assert report["ok"] is False
    assert "not valid JSON" in report["message"]
    assert report["scenarios_tested"] == 0


def test_scenario_file_wrong_top_level_shape_fails_gracefully(tmp_path):
    """A bare JSON array (instead of {"scenarios": [...]}) is rejected safely."""
    scenario_file = tmp_path / "array_shape.json"
    scenario_file.write_text(json.dumps([_base_scenario()]), encoding="utf-8")
    report_path = tmp_path / "report.json"

    report = run_fixture_mode(
        scenarios_path=str(scenario_file), report_path=str(report_path), clock=fixed_clock
    )

    assert report["ok"] is False
    assert "top-level 'scenarios' list" in report["message"]


def test_unknown_post_review_approval_status_is_rejected(tmp_path):
    """Mirrors the unknown-workflow_status test, but for the independently
    validated post_review.expected_approval_status field. Uses a distinct
    owner/pet profile from the shared default fixture for variety.
    """
    bad_scenario = _base_scenario(
        id="bad_post_review_scenario",
        owner={"name": "Kwame", "startTime": "06:00", "endTime": "22:00", "preferences": {}},
        pets=[
            {"petId": "pet-nomad-1", "name": "Nomad", "species": "Ferret", "breed": "Standard",
             "age": 3, "foodType": "Ferret kibble", "medication": "none", "energyLevel": 9,
             "careNeeds": []}
        ],
        tasks=[
            {"taskId": "feed-nomad-1", "taskName": "Feeding", "taskType": "feeding",
             "durationMinutes": 15, "priority": "medium", "petId": "pet-nomad-1",
             "preferredTime": "09:00", "recurrence": "none", "dueDate": "today",
             "completed": False, "notes": "", "flexibility": "preferred"}
        ],
    )
    bad_scenario["post_review"] = {
        "mutate": "change_time",
        "task_id": "feed-nomad-1",
        "new_time": "10:00",
        "expected_approval_status": "not_a_real_approval_status",
    }

    scenario_file = tmp_path / "scenarios.json"
    scenario_file.write_text(json.dumps({"scenarios": [bad_scenario]}), encoding="utf-8")
    report_path = tmp_path / "report.json"

    report = run_fixture_mode(
        scenarios_path=str(scenario_file), report_path=str(report_path), clock=fixed_clock
    )

    assert report["scenario_errors"] == 1
    assert "not a real ApprovalStatus" in report["records"][0]["error"]


def test_run_live_mode_with_unreadable_scenario_file_fails_gracefully():
    """A live-mode call still fails gracefully when the scenarios path itself
    is missing -- exercised with a syntactically valid but nonexistent-key
    placeholder so no real network call is ever attempted.
    """
    report = run_live_mode(
        scenarios_path="does/not/exist/scenarios.json",
        api_key="test-key-not-actually-valid",
        clock=fixed_clock,
    )
    assert report["ok"] is False
    assert report["mode"] == "live"


def test_harness_handles_unicode_names_and_midnight_adjacent_boundary_times(tmp_path):
    """Diverse, rare-but-legal input: a non-ASCII owner/pet name, an
    all-day availability window, and a task ending exactly at the window's
    closing minute -- none of this should crash the harness or the
    underlying Scheduler/validator boundary maths.
    """
    scenario = _base_scenario(
        id="unicode_and_boundary_scenario",
        owner={
            "name": "Amélie Œ",
            "startTime": "00:00",
            "endTime": "23:59",
            "preferences": {"language": "français"},
        },
        pets=[
            {"petId": "pet-tortoise-1", "name": "Törtle 🐢", "species": "Tortoise",
             "breed": "Sulcata", "age": 12, "foodType": "Leafy greens", "medication": "none",
             "energyLevel": 1, "careNeeds": ["UV lamp"]}
        ],
        tasks=[
            {"taskId": "feed-boundary-1", "taskName": "Late Feeding", "taskType": "feeding",
             "durationMinutes": 1, "priority": "low", "petId": "pet-tortoise-1",
             "preferredTime": "23:58", "recurrence": "none", "dueDate": "today",
             "completed": False, "notes": "", "flexibility": "preferred"}
        ],
    )

    scenario_file = tmp_path / "unicode_scenario.json"
    scenario_file.write_text(
        json.dumps({"scenarios": [scenario]}, ensure_ascii=False), encoding="utf-8"
    )
    report_path = tmp_path / "report.json"

    report = run_fixture_mode(
        scenarios_path=str(scenario_file), report_path=str(report_path), clock=fixed_clock
    )

    assert report["scenario_errors"] == 0
    record = report["records"][0]
    assert record["error"] is None
    assert record["actual_workflow_status"] == "no_repair_needed"
    assert record["mutation_safe"] is True
