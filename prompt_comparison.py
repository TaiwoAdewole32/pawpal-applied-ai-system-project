"""PawPal Sentinel baseline-vs-specialized prompt comparison (Phase 7.4).

This measures how the SAME two deterministic trust boundaries PawPal Sentinel
already relies on -- `RepairResult.from_dict`'s strict Phase 4.3 response
schema, then `ScheduleValidator.validate`'s Phase 2 guardrails -- treat two
different raw repair proposals for each of five representative conflict
scenarios already committed in `data/evaluation_scenarios.json`:

- "baseline": what a generic, unconstrained prompt ("Review this pet-care
  schedule and improve it.") plausibly returns. In fixture mode these are
  hand-authored per scenario to represent realistic failure modes the
  implementation plan itself calls out (moving a protected/fixed task, using
  an unsupported action instead of "defer_for_review", adding an extra
  unlisted field, using a malformed time string).
- "specialized": PawPal Sentinel's actual constrained repair-agent output for
  that same scenario -- reused directly from the scenario's own
  `ai_responses.repair[-1]` (its final, already-tested, correct attempt).

Fixture mode requires no API key and is fully reproducible -- it never calls
an LLM. Live mode (optional) sends the literal baseline prompt and the real
`REPAIR_AGENT_SYSTEM_PROMPT` to one Gemini call each per scenario, using an
identical payload shape so the prompt text is the only independent variable,
then measures both raw results the same way. Live mode never replaces
fixture-mode numbers as the reproducible evidence, matching the same
principle already established for `evaluate.py --mode live`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ai_client import AIConfigError, GeminiAIClient
from evaluate import (
    DEFAULT_SCENARIOS_PATH,
    ScenarioDefinitionError,
    _build_owner_from_scenario,
    _load_scenario_file,
    _write_report,
)
from repair_agent import REPAIR_AGENT_SYSTEM_PROMPT, REPAIR_PROMPT_VERSION
from schedule_validator import ScheduleValidator
from sentinel_models import (
    AIResponseValidationError,
    CriticResult,
    RepairResult,
    build_schedule_snapshot,
    schedule_snapshot_to_dict,
)

BASELINE_SYSTEM_PROMPT = "Review this pet-care schedule and improve it."
DEFAULT_REPORT_JSON = "reports/prompt_comparison.json"
DEFAULT_REPORT_MD = "reports/prompt_comparison.md"

# Five scenarios with genuine repairable conflicts, each chosen so the
# hand-authored baseline response below demonstrates a DISTINCT realistic
# failure mode rather than repeating the same guardrail five times.
_COMPARISON_SCENARIO_IDS = (
    "medication_conflict_with_flexible_walk",
    "two_fixed_tasks_overlap",
    "flexible_walk_outside_availability",
    "repair_creates_new_conflict",
    "capacity_exceeds_availability_window",
)

_BASELINE_RESPONSES: dict[str, dict] = {
    # Moves the fixed medication task itself -- a generic prompt has no
    # concept of a protected task type.
    "medication_conflict_with_flexible_walk": {
        "proposed_changes": [
            {
                "task_id": "med-p1",
                "action": "move",
                "original_time": "08:00",
                "new_time": "08:30",
                "reason": "Adjust the medication time to avoid the overlap.",
            }
        ],
        "summary": "Move the medication task earlier.",
    },
    # Moves one of two fixed tasks -- a generic prompt has no concept of
    # "defer_for_review" for an unresolvable fixed/fixed conflict.
    "two_fixed_tasks_overlap": {
        "proposed_changes": [
            {
                "task_id": "vet-m1",
                "action": "move",
                "original_time": "10:00",
                "new_time": "10:30",
                "reason": "Move the checkup earlier to avoid the conflict.",
            }
        ],
        "summary": "Move the veterinary checkup.",
    },
    # Uses a non-zero-padded, 12-hour time string -- a generic prompt has no
    # concept of the strict HH:MM 24-hour contract.
    "flexible_walk_outside_availability": {
        "proposed_changes": [
            {
                "task_id": "play-s1",
                "action": "move",
                "original_time": "07:30",
                "new_time": "8:00 AM",
                "reason": "Move the play session a little later.",
            }
        ],
        "summary": "Adjust the play session time.",
    },
    # Adds an extra, unlisted field -- a generic prompt has no concept of the
    # protected-field allowlist, so this fails RepairResult's strict schema
    # before the validator even runs.
    "repair_creates_new_conflict": {
        "proposed_changes": [
            {
                "task_id": "play-t1",
                "action": "move",
                "original_time": "12:10",
                "new_time": "13:05",
                "new_duration": 45,
                "reason": "Move play later and extend it.",
            }
        ],
        "summary": "Move and extend the play session.",
    },
    # Uses an action outside {move, keep, defer_for_review} -- a generic
    # prompt has no concept of the allowed-action enum.
    "capacity_exceeds_availability_window": {
        "proposed_changes": [
            {
                "task_id": "groom-o1",
                "action": "reschedule",
                "original_time": "11:00",
                "new_time": "11:00",
                "reason": "Reschedule the grooming session for another day.",
            }
        ],
        "summary": "Reschedule the grooming session.",
    },
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _measure(snapshot, raw_response: object) -> dict:
    """Run one raw AI-shaped response through the real validator/schema boundary.

    Never raises: a `RepairResult.from_dict` failure is captured as
    `structured_output_valid=False`, and `ScheduleValidator.validate` already
    tolerates a non-list/`None` proposed-changes value without raising (it
    just reports a schema error), so this is safe even for a wildly malformed
    or non-dict baseline response.
    """
    structured_output_valid = True
    structured_output_error: str | None = None
    try:
        RepairResult.from_dict(raw_response, max_changes=len(snapshot.tasks))
    except (AIResponseValidationError, TypeError, ValueError) as exc:
        structured_output_valid = False
        structured_output_error = f"{type(exc).__name__}: {exc}"

    proposed_changes = raw_response.get("proposed_changes") if isinstance(raw_response, dict) else None
    validation = ScheduleValidator().validate(snapshot, proposed_changes)
    checks = dict(validation.checks)

    return {
        "structured_output_valid": structured_output_valid,
        "structured_output_error": structured_output_error,
        "validator_valid": validation.valid,
        "checks": checks,
        "fixed_tasks_preserved": bool(
            checks.get("fixed_tasks_unchanged") and checks.get("medication_tasks_unchanged")
        ),
        "unknown_task_ids_present": checks.get("task_ids_known") is False,
        "conflict_free_accepted": bool(validation.valid and checks.get("no_new_conflicts")),
    }


def _aggregate(per_scenario: list[dict], condition: str) -> dict:
    total = len(per_scenario)
    rows = [row[condition] for row in per_scenario]
    return {
        "valid_structured_outputs": [sum(1 for r in rows if r["structured_output_valid"]), total],
        "fixed_tasks_preserved": [sum(1 for r in rows if r["fixed_tasks_preserved"]), total],
        "unsafe_proposals": [sum(1 for r in rows if not r["validator_valid"]), total],
        "unknown_task_ids": [sum(1 for r in rows if r["unknown_task_ids_present"]), total],
        "conflict_free_accepted": [sum(1 for r in rows if r["conflict_free_accepted"]), total],
    }


def _load_comparison_scenarios(scenarios_path: str) -> dict[str, dict]:
    raw_scenarios = _load_scenario_file(scenarios_path)
    scenarios_by_id = {
        scenario["id"]: scenario
        for scenario in raw_scenarios
        if isinstance(scenario, dict) and isinstance(scenario.get("id"), str)
    }
    missing = [sid for sid in _COMPARISON_SCENARIO_IDS if sid not in scenarios_by_id]
    if missing:
        raise ScenarioDefinitionError(
            f"Comparison scenario(s) not found in '{scenarios_path}': {', '.join(missing)}."
        )
    return scenarios_by_id


def run_fixture_comparison(
    scenarios_path: str = DEFAULT_SCENARIOS_PATH,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> dict:
    """Compare baseline vs specialized repair proposals with no API key required."""
    try:
        scenarios_by_id = _load_comparison_scenarios(scenarios_path)
    except ScenarioDefinitionError as exc:
        return {"ok": False, "mode": "fixture", "generated_at": _iso_utc(clock()), "message": str(exc)}

    per_scenario: list[dict] = []
    for scenario_id in _COMPARISON_SCENARIO_IDS:
        scenario = scenarios_by_id[scenario_id]
        try:
            owner = _build_owner_from_scenario(scenario)
            owner.scheduler.generatePlan()
            snapshot = build_schedule_snapshot(owner)
        except Exception as exc:  # noqa: BLE001 -- report, don't crash the whole comparison
            return {
                "ok": False,
                "mode": "fixture",
                "generated_at": _iso_utc(clock()),
                "message": f"Could not build scenario '{scenario_id}' ({type(exc).__name__}: {exc}).",
            }

        baseline_raw = _BASELINE_RESPONSES[scenario_id]
        specialized_raw = scenario["ai_responses"]["repair"][-1]

        per_scenario.append(
            {
                "id": scenario_id,
                "baseline": _measure(snapshot, baseline_raw),
                "specialized": _measure(snapshot, specialized_raw),
            }
        )

    summary = {
        "baseline": _aggregate(per_scenario, "baseline"),
        "specialized": _aggregate(per_scenario, "specialized"),
    }

    return {
        "ok": True,
        "mode": "fixture",
        "generated_at": _iso_utc(clock()),
        "baseline_prompt": BASELINE_SYSTEM_PROMPT,
        "specialized_prompt_version": REPAIR_PROMPT_VERSION,
        "scenario_ids": list(_COMPARISON_SCENARIO_IDS),
        "per_scenario": per_scenario,
        "summary": summary,
    }


def run_live_comparison(
    scenarios_path: str = DEFAULT_SCENARIOS_PATH,
    *,
    api_key: str | None = None,
    model_name: str | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> dict:
    """Compare baseline vs specialized repair proposals using the real Gemini model.

    Sends the identical payload shape to both prompts so the prompt text is
    the only independent variable. Fails gracefully (no exception) when the
    AI client cannot be configured, most commonly a missing GEMINI_API_KEY.
    """
    try:
        client = GeminiAIClient(api_key=api_key, model_name=model_name)
    except AIConfigError as exc:
        return {"ok": False, "mode": "live", "generated_at": _iso_utc(clock()), "message": str(exc)}

    try:
        scenarios_by_id = _load_comparison_scenarios(scenarios_path)
    except ScenarioDefinitionError as exc:
        return {"ok": False, "mode": "live", "generated_at": _iso_utc(clock()), "message": str(exc)}

    def _call(system_prompt: str, payload: dict) -> object:
        try:
            return client.generate_json(system_prompt, payload)
        except Exception:  # noqa: BLE001 -- an unconstrained baseline prompt may not return JSON at all
            return None

    per_scenario: list[dict] = []
    for scenario_id in _COMPARISON_SCENARIO_IDS:
        scenario = scenarios_by_id[scenario_id]
        try:
            owner = _build_owner_from_scenario(scenario)
            owner.scheduler.generatePlan()
            snapshot = build_schedule_snapshot(owner)
            known_task_ids = {task.task_id for task in snapshot.tasks}
            critic_result = CriticResult.from_dict(
                scenario["ai_responses"]["critic"],
                known_task_ids=known_task_ids,
                known_rule_sections=set(),
            )
            issue_task_ids = sorted(
                {task_id for issue in critic_result.issues for task_id in issue.task_ids}
            )
            payload = {
                "schedule": schedule_snapshot_to_dict(snapshot),
                "critic_result": critic_result.to_dict(),
                "allowed_issue_task_ids": issue_task_ids,
                "care_rules": [],
            }
        except Exception as exc:  # noqa: BLE001 -- report, don't crash the whole comparison
            per_scenario.append({"id": scenario_id, "error": f"{type(exc).__name__}: {exc}"})
            continue

        baseline_raw = _call(BASELINE_SYSTEM_PROMPT, {**payload, "prompt_version": "baseline"})
        specialized_raw = _call(
            REPAIR_AGENT_SYSTEM_PROMPT, {**payload, "prompt_version": REPAIR_PROMPT_VERSION}
        )
        per_scenario.append(
            {
                "id": scenario_id,
                "baseline": _measure(snapshot, baseline_raw),
                "specialized": _measure(snapshot, specialized_raw),
            }
        )

    scored = [row for row in per_scenario if "error" not in row]
    summary = (
        {
            "baseline": _aggregate(scored, "baseline"),
            "specialized": _aggregate(scored, "specialized"),
        }
        if scored
        else {}
    )

    return {
        "ok": True,
        "mode": "live",
        "generated_at": _iso_utc(clock()),
        "model_name": getattr(client, "model_name", None),
        "baseline_prompt": BASELINE_SYSTEM_PROMPT,
        "specialized_prompt_version": REPAIR_PROMPT_VERSION,
        "scenario_ids": list(_COMPARISON_SCENARIO_IDS),
        "per_scenario": per_scenario,
        "summary": summary,
        "note": (
            "Live mode records actual model behavior only. It is non-deterministic "
            "and never replaces fixture-mode reliability evidence."
        ),
    }


_METRIC_LABELS = (
    ("valid_structured_outputs", "Valid structured outputs"),
    ("fixed_tasks_preserved", "Fixed tasks preserved"),
    ("unsafe_proposals", "Unsafe proposals"),
    ("unknown_task_ids", "Unknown task IDs"),
    ("conflict_free_accepted", "Conflict-free accepted plans"),
)


def write_markdown_table(report: dict, path: str) -> None:
    summary = report.get("summary") or {}
    baseline = summary.get("baseline", {})
    specialized = summary.get("specialized", {})

    lines = [
        "# PawPal Sentinel: Baseline vs Specialized Prompt Comparison",
        "",
        f"Mode: {report.get('mode', 'fixture')}",
        "",
        f"Baseline prompt: `{report.get('baseline_prompt', BASELINE_SYSTEM_PROMPT)}`",
        "",
        f"Specialized prompt version: `{report.get('specialized_prompt_version', REPAIR_PROMPT_VERSION)}`",
        "",
        f"Scenarios compared: {', '.join(report.get('scenario_ids', []))}",
        "",
        "| Metric | Baseline | Specialized |",
        "|---|---|---|",
    ]
    for key, label in _METRIC_LABELS:
        b_count, b_total = baseline.get(key, [0, 0])
        s_count, s_total = specialized.get(key, [0, 0])
        lines.append(f"| {label} | {b_count}/{b_total} | {s_count}/{s_total} |")
    lines.append("")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")


def print_comparison(report: dict) -> None:
    print("PawPal Sentinel Prompt Comparison")
    print("===================================")
    print()

    if not report.get("ok", False):
        print(report.get("message", "Prompt comparison could not run."))
        if report.get("mode") == "live":
            print("Run 'python prompt_comparison.py --mode fixture' for reproducible evidence.")
        return

    print(f"Mode: {report['mode']}")
    print(f"Baseline prompt: {report['baseline_prompt']!r}")
    print(f"Specialized prompt version: {report['specialized_prompt_version']}")
    print(f"Scenarios compared: {', '.join(report['scenario_ids'])}")
    print()

    summary = report.get("summary") or {}
    baseline = summary.get("baseline", {})
    specialized = summary.get("specialized", {})
    header = f"{'Metric':<30}{'Baseline':<12}{'Specialized':<12}"
    print(header)
    print("-" * len(header))
    for key, label in _METRIC_LABELS:
        b_count, b_total = baseline.get(key, [0, 0])
        s_count, s_total = specialized.get(key, [0, 0])
        print(f"{label:<30}{f'{b_count}/{b_total}':<12}{f'{s_count}/{s_total}':<12}")

    if report.get("mode") == "live":
        print()
        print(report.get("note", ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PawPal Sentinel Phase 7.4 baseline-vs-specialized prompt comparison."
    )
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--scenarios", default=DEFAULT_SCENARIOS_PATH)
    parser.add_argument("--report-json", default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", default=DEFAULT_REPORT_MD)
    args = parser.parse_args(argv)

    if args.mode == "fixture":
        report = run_fixture_comparison(scenarios_path=args.scenarios)
    else:
        report = run_live_comparison(scenarios_path=args.scenarios)

    print_comparison(report)
    if not report.get("ok", False):
        return 1

    _write_report(report, args.report_json)
    write_markdown_table(report, args.report_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
