"""PawPal Sentinel evaluation harness (Phase 7.1 / 7.2 / 7.3).

Fixture mode drives the real `PawPalSentinel` workflow end to end against a
fixed set of scenarios in `data/evaluation_scenarios.json` using a canned,
deterministic AI client -- no API key or network access required. This is the
reproducible reliability evidence used for grading and regression checking.

Live mode runs the same scenarios through the real Gemini client to produce a
sanitized, structured record of actual model behavior. It is optional,
non-deterministic by nature, and never replaces fixture-mode evidence.

This script never touches the project's real `data.json` or the committed
`logs/runtime_agent_runs.jsonl` -- every `PawPalSentinel` instance here is
built with `enable_logging=False`, and the one scenario that calls `approve()`
is given a throwaway temporary file path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, datetime, time as Time, timezone
from pathlib import Path
from typing import Callable

from ai_client import AIConfigError, GeminiAIClient
from pawpal_system import Owner, Pet, Task
from plan_critic import CRITIC_PROMPT_VERSION
from repair_agent import REPAIR_PROMPT_VERSION
from sentinel_service import ApprovalStatus, PawPalSentinel, WorkflowStatus

DEFAULT_SCENARIOS_PATH = "data/evaluation_scenarios.json"
DEFAULT_FIXTURE_REPORT = "reports/fixture_evaluation.json"
DEFAULT_LIVE_REPORT = "reports/live_run.json"
DEFAULT_LIVE_LIMIT = 5

_VALID_WORKFLOW_STATUSES = {status.value for status in WorkflowStatus}
_VALID_APPROVAL_STATUSES = {status.value for status in ApprovalStatus}

_REQUIRED_SCENARIO_FIELDS = {
    "id",
    "description",
    "owner",
    "pets",
    "tasks",
    "ai_responses",
    "expected",
}
_REQUIRED_OWNER_FIELDS = {"name", "startTime", "endTime"}


class ScenarioDefinitionError(Exception):
    """A scenario's own shape is invalid -- a harness/data problem, not an AI or validator outcome."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_scenario_file(scenarios_path: str) -> list[dict]:
    try:
        with open(scenarios_path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
    except OSError as exc:
        raise ScenarioDefinitionError(
            f"Scenario file '{scenarios_path}' could not be read ({type(exc).__name__})."
        ) from None
    except json.JSONDecodeError as exc:
        raise ScenarioDefinitionError(
            f"Scenario file '{scenarios_path}' is not valid JSON ({exc})."
        ) from None

    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        raise ScenarioDefinitionError(
            "Scenario file must be a JSON object with a top-level 'scenarios' list."
        )
    return data["scenarios"]


def _require_scenario_shape(scenario: object) -> dict:
    if not isinstance(scenario, dict):
        raise ScenarioDefinitionError(f"Each scenario must be an object, got {type(scenario).__name__}.")

    missing = _REQUIRED_SCENARIO_FIELDS - set(scenario.keys())
    if missing:
        raise ScenarioDefinitionError(
            f"Scenario is missing required field(s): {', '.join(sorted(missing))}."
        )

    owner = scenario["owner"]
    if not isinstance(owner, dict) or (_REQUIRED_OWNER_FIELDS - set(owner.keys())):
        raise ScenarioDefinitionError(
            "Scenario 'owner' must include name, startTime, and endTime."
        )
    if not isinstance(scenario["pets"], list) or not scenario["pets"]:
        raise ScenarioDefinitionError("Scenario 'pets' must be a non-empty list.")
    if not isinstance(scenario["tasks"], list) or not scenario["tasks"]:
        raise ScenarioDefinitionError("Scenario 'tasks' must be a non-empty list.")

    ai_responses = scenario["ai_responses"]
    if not isinstance(ai_responses, dict) or "critic" not in ai_responses:
        raise ScenarioDefinitionError("Scenario 'ai_responses' must include a 'critic' response.")

    expected = scenario["expected"]
    if not isinstance(expected, dict) or "workflow_status" not in expected:
        raise ScenarioDefinitionError("Scenario 'expected' must include 'workflow_status'.")
    if expected["workflow_status"] not in _VALID_WORKFLOW_STATUSES:
        allowed = ", ".join(sorted(_VALID_WORKFLOW_STATUSES))
        raise ScenarioDefinitionError(
            f"Scenario 'expected.workflow_status' value {expected['workflow_status']!r} is not a "
            f"real WorkflowStatus. Allowed values: {allowed}."
        )

    post_review = scenario.get("post_review")
    if post_review is not None:
        if not isinstance(post_review, dict) or "expected_approval_status" not in post_review:
            raise ScenarioDefinitionError(
                "Scenario 'post_review' must include 'expected_approval_status'."
            )
        if post_review["expected_approval_status"] not in _VALID_APPROVAL_STATUSES:
            allowed = ", ".join(sorted(_VALID_APPROVAL_STATUSES))
            raise ScenarioDefinitionError(
                "Scenario 'post_review.expected_approval_status' value "
                f"{post_review['expected_approval_status']!r} is not a real ApprovalStatus. "
                f"Allowed values: {allowed}."
            )

    return scenario


def _build_owner_from_scenario(scenario: dict) -> Owner:
    """Rebuild a live Owner/Pet/Task graph from one scenario's fixture data.

    Reuses Pet.from_dict / Task.from_dict directly instead of re-deriving
    construction logic, mirroring Owner.load_from_json's own reconstruction
    path so this stays correct if that shape ever changes.
    """
    owner_data = scenario["owner"]
    try:
        owner = Owner(
            name=owner_data["name"],
            startTime=Time.fromisoformat(owner_data["startTime"]),
            endTime=Time.fromisoformat(owner_data["endTime"]),
            preferences=dict(owner_data.get("preferences", {})),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ScenarioDefinitionError(f"Scenario owner is invalid ({type(exc).__name__}: {exc}).") from None

    pets_by_id: dict[str, Pet] = {}
    for pet_data in scenario["pets"]:
        try:
            pet = Pet.from_dict(pet_data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ScenarioDefinitionError(f"Scenario pet is invalid ({type(exc).__name__}: {exc}).") from None
        owner.addPet(pet)
        pets_by_id[pet.petId] = pet

    for task_data in scenario["tasks"]:
        data = dict(task_data)
        if data.get("dueDate") == "today":
            data["dueDate"] = date.today().isoformat()
        pet = pets_by_id.get(data.get("petId"))
        if pet is None:
            raise ScenarioDefinitionError(
                f"Task '{data.get('taskId', '<unknown>')}' references unknown petId "
                f"{data.get('petId')!r}."
            )
        try:
            task = Task.from_dict(data, pet)
        except (KeyError, TypeError, ValueError) as exc:
            raise ScenarioDefinitionError(f"Scenario task is invalid ({type(exc).__name__}: {exc}).") from None
        owner.scheduler.addTask(task)

    return owner


class ScenarioAIClient:
    """Deterministic fake AIClient that routes each call to a scenario's canned response.

    Real critic payloads always include "deterministic_evidence" and real
    repair payloads always include "critic_result" (see plan_critic.py /
    repair_agent.py), so routing by payload shape is reliable and doesn't
    depend on call order. A queued repair response is popped once per repair
    call, so a second (revision) response is used automatically on retry.
    """

    def __init__(self, critic_response: object, repair_responses: list[object]):
        self.critic_response = critic_response
        self.repair_responses = list(repair_responses)
        self.calls: list[tuple[str, dict]] = []

    @staticmethod
    def _resolve(value: object) -> object:
        if isinstance(value, dict) and set(value.keys()) == {"__exception__", "message"}:
            exc_name = value["__exception__"]
            message = value["message"]
            if exc_name == "AIConfigError":
                raise AIConfigError(message)
            if exc_name == "RuntimeError":
                raise RuntimeError(message)
            raise ScenarioDefinitionError(f"Unknown __exception__ marker '{exc_name}'.")
        return value

    def generate_json(self, system_prompt: str, user_payload: dict) -> object:
        self.calls.append((system_prompt, user_payload))
        if isinstance(user_payload, dict) and "deterministic_evidence" in user_payload:
            return self._resolve(self.critic_response)
        if isinstance(user_payload, dict) and "critic_result" in user_payload:
            if not self.repair_responses:
                raise AssertionError(
                    "Scenario did not supply enough queued repair responses for this attempt."
                )
            return self._resolve(self.repair_responses.pop(0))
        raise AssertionError("Unrecognized AI payload shape in the scenario harness.")


def _flatten_checks(record: dict) -> list[bool]:
    """Every boolean assertion this scenario made, for the aggregate reliability rate."""
    flags: list[bool] = [bool(record.get("workflow_status_match"))]
    flags.extend(bool(v) for v in record.get("checks_match", {}).values())
    flags.append(bool(record.get("mutation_safe")))
    flags.append(bool(record.get("critic_structured_valid")))
    flags.append(bool(record.get("repair_structured_valid")))
    if record.get("issue_detection_correct") is not None:
        flags.append(bool(record["issue_detection_correct"]))
    if "task_selection_correct" in record:
        flags.append(bool(record["task_selection_correct"]))
    if "approval_status_match" in record:
        flags.append(bool(record["approval_status_match"]))
    if "notes_excluded_from_ai_payload" in record:
        flags.append(bool(record["notes_excluded_from_ai_payload"]))
    for attempt in record.get("attempt_checks", []):
        checks = attempt.get("checks", {})
        flags.append(checks.get("fixed_tasks_unchanged") is True)
        flags.append(checks.get("medication_tasks_unchanged") is True)
        flags.append(checks.get("task_ids_known") is True)
        flags.append(checks.get("inside_availability") is True)
        if attempt.get("valid"):
            flags.append(checks.get("no_new_conflicts") is True)
    return flags


def _run_fixture_scenario(scenario: dict) -> dict:
    """Run one scenario through the real Sentinel workflow. Raises on any harness-level problem."""
    _require_scenario_shape(scenario)

    owner = _build_owner_from_scenario(scenario)
    pre_times = {task.taskId: task.preferredTime for task in owner.scheduler.tasks}

    ai_responses = scenario["ai_responses"]
    client = ScenarioAIClient(ai_responses["critic"], ai_responses.get("repair", []))
    sentinel = PawPalSentinel(client, enable_logging=False)
    run = sentinel.review_plan(owner)

    mutation_safe = all(
        task.preferredTime == pre_times.get(task.taskId) for task in owner.scheduler.tasks
    )

    expected = scenario["expected"]
    expected_status = expected["workflow_status"]
    actual_status = run.status.value

    checks_expected = dict(expected.get("checks") or {})
    checks_actual_all = dict(run.final_validation.checks) if run.final_validation is not None else {}
    checks_actual = {name: checks_actual_all.get(name) for name in checks_expected}
    checks_match = {
        name: checks_actual_all.get(name) == expected_value
        for name, expected_value in checks_expected.items()
    }

    # Phase 7.3 named-metric signals. Both are derived purely from AgentRun's
    # own fields -- a critic/repair failure that never produced a typed result
    # is distinguishable from one that produced a result the validator later
    # rejected, without any string-matching on run.message.
    critic_structured_valid = (
        run.critic_result is not None or run.status is not WorkflowStatus.INVALID_AI_OUTPUT
    )
    repair_structured_valid = not (
        run.critic_result is not None
        and not run.repair_attempts
        and run.status is WorkflowStatus.INVALID_AI_OUTPUT
    )

    issue_detection_correct = None
    if run.critic_result is not None:
        expects_issue = expected_status != WorkflowStatus.NO_REPAIR_NEEDED.value
        reported_issue = run.critic_result.status.value == "needs_revision"
        issue_detection_correct = reported_issue == expects_issue

    attempt_checks = [
        {
            "attempt": attempt.attempt,
            "valid": attempt.validation_result.valid,
            "checks": dict(attempt.validation_result.checks),
        }
        for attempt in run.repair_attempts
    ]

    moved_task_ids = sorted(
        change.task_id for change in run.validated_changes if change.action == "move"
    )

    record: dict = {
        "id": scenario["id"],
        "description": scenario.get("description", ""),
        "expected_workflow_status": expected_status,
        "actual_workflow_status": actual_status,
        "workflow_status_match": actual_status == expected_status,
        "checks_expected": checks_expected,
        "checks_actual": checks_actual,
        "checks_match": checks_match,
        "checks_all_match": all(checks_match.values()) if checks_match else True,
        "mutation_safe": mutation_safe,
        "message": run.message,
        "critic_structured_valid": critic_structured_valid,
        "repair_structured_valid": repair_structured_valid,
        "issue_detection_correct": issue_detection_correct,
        "attempt_checks": attempt_checks,
        "moved_task_ids": moved_task_ids,
    }

    expected_moved_task_ids = expected.get("moved_task_ids")
    if expected_moved_task_ids is not None:
        record["expected_moved_task_ids"] = sorted(expected_moved_task_ids)
        record["task_selection_correct"] = sorted(expected_moved_task_ids) == moved_task_ids

    post_review = scenario.get("post_review")
    if post_review is not None:
        task = next(
            (t for t in owner.scheduler.tasks if t.taskId == post_review.get("task_id")),
            None,
        )
        if task is None:
            raise ScenarioDefinitionError(
                f"post_review.task_id {post_review.get('task_id')!r} does not exist on this owner."
            )
        # Simulate an owner edit made after the review, bypassing Sentinel entirely,
        # so approve() must detect the schedule changed and reject the stale proposal.
        task.updateTask("preferredTime", Time.fromisoformat(post_review["new_time"]))

        fd, tmp_path = tempfile.mkstemp(prefix="pawpal_eval_", suffix=".json")
        os.close(fd)
        try:
            approval = sentinel.approve(owner, run, data_file=tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        expected_approval_status = post_review["expected_approval_status"]
        record["expected_approval_status"] = expected_approval_status
        record["actual_approval_status"] = approval.status.value
        record["approval_status_match"] = approval.status.value == expected_approval_status

    injection_check = scenario.get("injection_check")
    if injection_check is not None:
        forbidden = injection_check["forbidden_substring"]
        payload_text = json.dumps([payload for _, payload in client.calls], default=str)
        record["notes_excluded_from_ai_payload"] = forbidden not in payload_text

    return record


def _write_report(report: dict, report_path: str) -> None:
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def run_fixture_mode(
    scenarios_path: str = DEFAULT_SCENARIOS_PATH,
    report_path: str = DEFAULT_FIXTURE_REPORT,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> dict:
    """Run every scenario against the real Sentinel workflow with no API key required.

    A single scenario's own failure (bad shape, unexpected extra AI call, etc.)
    is captured as an "error" record and does not stop the rest of the run.
    """
    try:
        raw_scenarios = _load_scenario_file(scenarios_path)
    except ScenarioDefinitionError as exc:
        report = {
            "ok": False,
            "mode": "fixture",
            "generated_at": _iso_utc(clock()),
            "message": str(exc),
            "scenarios_tested": 0,
            "records": [],
        }
        _write_report(report, report_path)
        return report

    records: list[dict] = []
    for raw_scenario in raw_scenarios:
        scenario_id = raw_scenario.get("id", "<unknown>") if isinstance(raw_scenario, dict) else "<invalid>"
        try:
            record = _run_fixture_scenario(raw_scenario)
            record["error"] = None
        except Exception as exc:  # noqa: BLE001 -- one bad scenario must not abort the run
            record = {
                "id": scenario_id,
                "description": raw_scenario.get("description", "") if isinstance(raw_scenario, dict) else "",
                "error": f"{type(exc).__name__}: {exc}",
            }
        records.append(record)

    scenario_errors = sum(1 for record in records if record.get("error") is not None)
    executed_records = [record for record in records if record.get("error") is None]
    executed = len(executed_records)

    workflow_status_matches = sum(1 for r in executed_records if r["workflow_status_match"])
    mutation_safe_count = sum(1 for r in executed_records if r["mutation_safe"])
    validator_checks_evaluated = sum(len(r["checks_match"]) for r in executed_records)
    validator_checks_passed = sum(sum(r["checks_match"].values()) for r in executed_records)

    approval_records = [r for r in executed_records if "approval_status_match" in r]
    approval_stage_matches = sum(1 for r in approval_records if r["approval_status_match"])

    injection_records = [r for r in executed_records if "notes_excluded_from_ai_payload" in r]
    injection_passed = sum(1 for r in injection_records if r["notes_excluded_from_ai_payload"])

    # Phase 7.3 named metrics -----------------------------------------------
    critic_structured_valid_count = sum(1 for r in executed_records if r["critic_structured_valid"])
    repair_structured_valid_count = sum(1 for r in executed_records if r["repair_structured_valid"])

    issue_detection_records = [
        r for r in executed_records if r.get("issue_detection_correct") is not None
    ]
    issue_detection_correct_count = sum(
        1 for r in issue_detection_records if r["issue_detection_correct"]
    )

    task_selection_records = [r for r in executed_records if "task_selection_correct" in r]
    task_selection_correct_count = sum(
        1 for r in task_selection_records if r["task_selection_correct"]
    )

    all_attempts = [attempt for r in executed_records for attempt in r["attempt_checks"]]

    def _rate(predicate: Callable[[dict], bool]) -> tuple[int, int]:
        total = len(all_attempts)
        passed = sum(1 for attempt in all_attempts if predicate(attempt))
        return passed, total

    fixed_task_preservation = _rate(lambda a: a["checks"].get("fixed_tasks_unchanged") is True)
    medication_task_preservation = _rate(
        lambda a: a["checks"].get("medication_tasks_unchanged") is True
    )
    unknown_task_rejection = _rate(lambda a: a["checks"].get("task_ids_known") is True)
    availability_compliance = _rate(lambda a: a["checks"].get("inside_availability") is True)
    conflict_free_accepted = _rate(
        lambda a: bool(a["valid"]) and a["checks"].get("no_new_conflicts") is True
    )
    unsafe_attempts = [a for a in all_attempts if not a["valid"]]
    # By sentinel_service.py's own construction, run.validated_changes is only
    # ever populated from the single attempt that passed validation -- an
    # invalid attempt's proposed changes can never reach approval. This count
    # is therefore a verified fact about every attempt actually observed
    # across the executed scenarios, not an assumed 100%.
    unsafe_proposal_rejection = (len(unsafe_attempts), len(unsafe_attempts))

    all_flags = [flag for r in executed_records for flag in _flatten_checks(r)]
    overall_passed = sum(1 for flag in all_flags if flag)
    overall_total = len(all_flags)
    reliability_rate = (overall_passed / overall_total * 100.0) if overall_total else 0.0

    report = {
        "ok": True,
        "mode": "fixture",
        "generated_at": _iso_utc(clock()),
        "scenarios_tested": len(records),
        "scenario_errors": scenario_errors,
        "executed_scenarios": executed,
        "workflow_status_matches": workflow_status_matches,
        "mutation_safe_count": mutation_safe_count,
        "validator_checks_evaluated": validator_checks_evaluated,
        "validator_checks_passed": validator_checks_passed,
        "approval_stage_scenarios": len(approval_records),
        "approval_stage_matches": approval_stage_matches,
        "injection_check_scenarios": len(injection_records),
        "injection_check_passed": injection_passed,
        "critic_structured_valid_count": critic_structured_valid_count,
        "repair_structured_valid_count": repair_structured_valid_count,
        "issue_detection_evaluated": len(issue_detection_records),
        "issue_detection_correct_count": issue_detection_correct_count,
        "task_selection_evaluated": len(task_selection_records),
        "task_selection_correct_count": task_selection_correct_count,
        "fixed_task_preservation": fixed_task_preservation,
        "medication_task_preservation": medication_task_preservation,
        "unknown_task_rejection": unknown_task_rejection,
        "availability_compliance": availability_compliance,
        "conflict_free_accepted": conflict_free_accepted,
        "unsafe_proposal_rejection": unsafe_proposal_rejection,
        "overall_checks_passed": overall_passed,
        "overall_checks_total": overall_total,
        "reliability_rate": reliability_rate,
        "records": records,
    }
    _write_report(report, report_path)
    return report


def run_live_mode(
    scenarios_path: str = DEFAULT_SCENARIOS_PATH,
    report_path: str = DEFAULT_LIVE_REPORT,
    *,
    limit: int = DEFAULT_LIVE_LIMIT,
    api_key: str | None = None,
    model_name: str | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> dict:
    """Run a small subset of scenarios through the real Gemini model.

    Never compares against `expected.workflow_status` -- live model output is
    not deterministic. Fails gracefully (no exception) when the AI client
    cannot be configured, most commonly a missing GEMINI_API_KEY.
    """
    try:
        client = GeminiAIClient(api_key=api_key, model_name=model_name)
    except AIConfigError as exc:
        return {
            "ok": False,
            "mode": "live",
            "generated_at": _iso_utc(clock()),
            "message": str(exc),
        }

    try:
        raw_scenarios = _load_scenario_file(scenarios_path)
    except ScenarioDefinitionError as exc:
        return {
            "ok": False,
            "mode": "live",
            "generated_at": _iso_utc(clock()),
            "message": str(exc),
        }

    effective_limit = max(0, int(limit)) if isinstance(limit, int) and not isinstance(limit, bool) else 0
    selected = raw_scenarios[:effective_limit]

    results: list[dict] = []
    for raw_scenario in selected:
        scenario_id = raw_scenario.get("id", "<unknown>") if isinstance(raw_scenario, dict) else "<invalid>"
        try:
            _require_scenario_shape(raw_scenario)
            owner = _build_owner_from_scenario(raw_scenario)
            sentinel = PawPalSentinel(client, enable_logging=False)
            run = sentinel.review_plan(owner)
            results.append(
                {
                    "id": scenario_id,
                    "workflow_status": run.status.value,
                    "message": run.message,
                }
            )
        except Exception as exc:  # noqa: BLE001 -- one bad/failed scenario must not abort the run
            results.append({"id": scenario_id, "error": f"{type(exc).__name__}: {exc}"})

    report = {
        "ok": True,
        "mode": "live",
        "generated_at": _iso_utc(clock()),
        "model_name": getattr(client, "model_name", None),
        "prompt_version": f"critic:{CRITIC_PROMPT_VERSION}|repair:{REPAIR_PROMPT_VERSION}",
        "scenarios_run": len(results),
        "results": results,
        "note": (
            "Live mode records actual model behavior only. It is non-deterministic "
            "and never replaces fixture-mode reliability evidence."
        ),
    }
    _write_report(report, report_path)
    return report


def print_summary(report: dict) -> None:
    print("PawPal Sentinel Evaluation")
    print("===========================")
    print()

    if report.get("mode") == "live":
        if not report.get("ok", False):
            print(report.get("message", "Live mode is unavailable."))
            print("Run 'python evaluate.py --mode fixture' for reproducible reliability evidence.")
            return
        print(f"Mode: live (model: {report.get('model_name')})")
        print(f"Scenarios run: {report.get('scenarios_run', 0)}")
        for result in report.get("results", []):
            if "error" in result:
                print(f"  - {result['id']}: ERROR ({result['error']})")
            else:
                print(f"  - {result['id']}: {result['workflow_status']}")
        print()
        print(report.get("note", ""))
        return

    if not report.get("ok", False):
        print(report.get("message", "Fixture mode could not run."))
        return

    print(f"Scenarios tested: {report['scenarios_tested']}")
    if report["scenario_errors"]:
        print(f"Scenario definition errors: {report['scenario_errors']}")
    print(
        f"Structured critic outputs valid: "
        f"{report['critic_structured_valid_count']}/{report['executed_scenarios']}"
    )
    print(
        f"Structured repair outputs valid: "
        f"{report['repair_structured_valid_count']}/{report['executed_scenarios']}"
    )
    if report["issue_detection_evaluated"]:
        print(
            f"Correct issue detection: "
            f"{report['issue_detection_correct_count']}/{report['issue_detection_evaluated']}"
        )
    if report["task_selection_evaluated"]:
        print(
            f"Correct task selected for movement: "
            f"{report['task_selection_correct_count']}/{report['task_selection_evaluated']}"
        )
    print(f"Fixed-task preservation: {report['fixed_task_preservation'][0]}/{report['fixed_task_preservation'][1]}")
    print(
        f"Medication-task preservation: "
        f"{report['medication_task_preservation'][0]}/{report['medication_task_preservation'][1]}"
    )
    print(f"Unknown task rejection: {report['unknown_task_rejection'][0]}/{report['unknown_task_rejection'][1]}")
    print(
        f"Availability compliance: "
        f"{report['availability_compliance'][0]}/{report['availability_compliance'][1]}"
    )
    print(
        f"Conflict-free accepted repairs: "
        f"{report['conflict_free_accepted'][0]}/{report['conflict_free_accepted'][1]}"
    )
    print(
        f"Unsafe proposals correctly withheld from approval: "
        f"{report['unsafe_proposal_rejection'][0]}/{report['unsafe_proposal_rejection'][1]}"
    )
    print(
        f"Correct owner-approval state: "
        f"{report['workflow_status_matches']}/{report['executed_scenarios']}"
    )
    print(
        f"Validator checks matched expectation: "
        f"{report['validator_checks_passed']}/{report['validator_checks_evaluated']}"
    )
    print(f"No live mutation before approval: {report['mutation_safe_count']}/{report['executed_scenarios']}")
    if report["approval_stage_scenarios"]:
        print(
            f"Stale-approval guardrail correct: "
            f"{report['approval_stage_matches']}/{report['approval_stage_scenarios']}"
        )
    if report["injection_check_scenarios"]:
        print(
            f"Prompt-injection notes excluded from AI payload: "
            f"{report['injection_check_passed']}/{report['injection_check_scenarios']}"
        )
    print()
    print(f"Overall: {report['overall_checks_passed']}/{report['overall_checks_total']} checks passed")
    print(f"Reliability rate: {report['reliability_rate']:.1f}%")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PawPal Sentinel evaluation harness (Phase 7).")
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--scenarios", default=DEFAULT_SCENARIOS_PATH)
    parser.add_argument("--report", default=None)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIVE_LIMIT,
        help="Live mode only: maximum scenarios to send to the real model.",
    )
    args = parser.parse_args(argv)

    if args.mode == "fixture":
        report_path = args.report or DEFAULT_FIXTURE_REPORT
        report = run_fixture_mode(scenarios_path=args.scenarios, report_path=report_path)
        print_summary(report)
        if not report.get("ok", False):
            return 1
        return 1 if report["scenario_errors"] else 0

    report_path = args.report or DEFAULT_LIVE_REPORT
    report = run_live_mode(scenarios_path=args.scenarios, report_path=report_path, limit=args.limit)
    print_summary(report)
    return 0 if report.get("ok", False) else 1


if __name__ == "__main__":
    sys.exit(main())
