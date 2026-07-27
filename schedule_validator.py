"""Deterministic validator for PawPal Sentinel repair proposals.

This is the trust boundary: it never talks to an AI client, never touches
`Owner`/`Scheduler`/`Task` directly, and never mutates anything. It only
reasons about a `ScheduleSnapshot` (Phase 2.1) and a raw proposed-changes
payload (as an AI would return it), and produces a `ValidationResult` the
caller can act on. Applying an accepted proposal to the live schedule is a
separate concern (Phase 2.5), not implemented here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime

from pawpal_system import FIXED_TASK_TYPES, Owner
from sentinel_models import (
    ProposedChange,
    ScheduleSnapshot,
    TaskSnapshot,
    ValidationResult,
    build_schedule_snapshot,
)

ALLOWED_ACTIONS = {"move", "keep", "defer_for_review"}
ALLOWED_CHANGE_FIELDS = {"task_id", "action", "original_time", "new_time", "reason"}
MAX_REASON_LENGTH = 500

# Strict zero-padded 24-hour HH:MM. Rejects "7:00", "7 PM", "19:75", "24:00", etc.
_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class Conflict:
    task_id_a: str
    task_id_b: str


def _time_to_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def find_conflicts(tasks: tuple[TaskSnapshot, ...]) -> list[Conflict]:
    """Pure helper: which task pairs in this candidate list overlap in time.

    Mirrors Scheduler.detectConflicts()'s existing overlap math (same
    time-of-day comparison, due_date-agnostic) but works on snapshots so it
    never touches Scheduler.tasks.
    """
    parsed = [
        (_time_to_minutes(t.preferred_time), t.duration_minutes, t.task_id)
        for t in tasks
    ]
    conflicts: list[Conflict] = []
    for i, (start_a, duration_a, id_a) in enumerate(parsed):
        end_a = start_a + duration_a
        for start_b, duration_b, id_b in parsed[i + 1:]:
            end_b = start_b + duration_b
            if start_a < end_b and start_b < end_a:
                conflicts.append(Conflict(task_id_a=id_a, task_id_b=id_b))
    return conflicts

class SentinelApplyError(Exception):
    """Base for apply_approved_changes() failures. Never raised with partial mutation applied."""


class StaleScheduleError(SentinelApplyError):
    """The schedule changed since this proposal was reviewed."""


class InvalidProposalError(SentinelApplyError):
    """Revalidating the approved changes against the current schedule failed."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _parse_time_string(value: str):
    """Inverse of sentinel_models._task_to_snapshot's preferred_time formatting."""
    return datetime.strptime(value, "%H:%M").time()


def apply_approved_changes(
    owner: Owner,
    snapshot_version: str,
    validated_changes: list[ProposedChange],
    data_file: str = "data.json",
) -> None:
    """Apply an already-validated proposal to the live schedule, or raise safely.

    Revalidates against a freshly-rebuilt snapshot before touching anything —
    this is what makes staleness protection real, not just a version-string
    comparison. Never partially mutates: every task is looked up and every
    time is parsed before any live task is changed, and any unexpected
    failure during the commit step rolls back everything already applied.
    """
    current_snapshot = build_schedule_snapshot(owner)
    if current_snapshot.version != snapshot_version:
        raise StaleScheduleError(
            "The schedule changed since this proposal was reviewed — request a new review."
        )

    proposal_dicts = [
        {
            "task_id": change.task_id,
            "action": change.action,
            "original_time": change.original_time,
            "new_time": change.new_time,
            "reason": change.reason,
        }
        for change in validated_changes
    ]
    result = ScheduleValidator().validate(current_snapshot, proposal_dicts)
    if not result.valid:
        raise InvalidProposalError(result.errors)

    tasks_by_id = {task.taskId: task for task in owner.scheduler.tasks}
    prepared = []  # (task, original_time, new_time) — nothing mutated yet
    for change in validated_changes:
        if change.action != "move":
            continue
        task = tasks_by_id.get(change.task_id)
        if task is None:
            raise InvalidProposalError([f"Task '{change.task_id}' no longer exists."])
        prepared.append((task, task.preferredTime, _parse_time_string(change.new_time)))

    applied = []
    try:
        for task, original_time, new_time in prepared:
            task.updateTask("preferredTime", new_time)
            applied.append((task, original_time))
    except Exception:
        for task, original_time in applied:
            task.updateTask("preferredTime", original_time)
        raise

    owner.save_to_json(data_file)


class ScheduleValidator:
    """Validates a proposed-changes payload against an exact schedule snapshot."""

    def validate(self, snapshot: ScheduleSnapshot, proposed_changes) -> ValidationResult:
        errors: list[str] = []
        checks = {
            "schema_valid": False,
            "task_ids_known": False,
            "actions_allowed": False,
            "fixed_tasks_unchanged": False,
            "medication_tasks_unchanged": False,
            "times_valid": False,
            "inside_availability": False,
            "no_new_conflicts": False,
            "protected_fields_unchanged": False,
            # Staleness is a Phase 2.5 concept: apply_approved_changes rebuilds
            # the snapshot and compares .version at approval time. validate()
            # always checks against whatever snapshot it's given, so there is
            # nothing to compare here yet — intentionally always True.
            "proposal_not_stale": True,
        }

        if not isinstance(proposed_changes, list):
            errors.append(
                f"Proposed changes must be a list, got {type(proposed_changes).__name__}."
            )
            return ValidationResult(valid=False, errors=errors, checks=checks, normalized_changes=[])

        # --- Availability window shape (2.4.7) ---
        window_ok = True
        avail_start = avail_end = None
        try:
            avail_start = _time_to_minutes(snapshot.availability_start)
            avail_end = _time_to_minutes(snapshot.availability_end)
        except (ValueError, AttributeError):
            window_ok = False
            errors.append("Owner availability window is malformed.")
        if window_ok and avail_end <= avail_start:
            window_ok = False
            errors.append(
                "Sentinel doesn't yet support overnight availability windows — "
                "use Generate Schedule instead."
            )

        # --- Schema checks (2.4.1) ---
        if len(proposed_changes) > len(snapshot.tasks):
            errors.append(
                f"Too many proposed changes ({len(proposed_changes)}) for "
                f"{len(snapshot.tasks)} known task(s)."
            )

        tasks_by_id = {t.task_id: t for t in snapshot.tasks}
        seen_task_ids: set[str] = set()
        schema_ok_items: list[dict] = []
        schema_all_valid = len(proposed_changes) <= len(snapshot.tasks)

        for index, item in enumerate(proposed_changes):
            label = f"item[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{label}: must be an object, got {type(item).__name__}.")
                schema_all_valid = False
                continue

            item_keys = set(item.keys())
            missing = ALLOWED_CHANGE_FIELDS - item_keys
            extra = item_keys - ALLOWED_CHANGE_FIELDS
            if missing:
                errors.append(f"{label}: missing required field(s): {', '.join(sorted(missing))}.")
                schema_all_valid = False
                continue
            if extra:
                errors.append(f"{label}: unknown field(s) not allowed: {', '.join(sorted(extra))}.")
                schema_all_valid = False
                continue

            task_id, action = item["task_id"], item["action"]
            original_time, new_time, reason = item["original_time"], item["new_time"], item["reason"]

            type_errors = []
            if not isinstance(task_id, str) or not task_id:
                type_errors.append("task_id must be a non-empty string")
            if not isinstance(action, str):
                type_errors.append("action must be a string")
            if original_time is not None and not isinstance(original_time, str):
                type_errors.append("original_time must be a string or null")
            if new_time is not None and not isinstance(new_time, str):
                type_errors.append("new_time must be a string or null")
            if not isinstance(reason, str):
                type_errors.append("reason must be a string")
            elif len(reason) > MAX_REASON_LENGTH:
                type_errors.append(f"reason exceeds {MAX_REASON_LENGTH} characters")

            if type_errors:
                errors.append(f"{label}: " + "; ".join(type_errors) + ".")
                schema_all_valid = False
                continue

            if task_id in seen_task_ids:
                errors.append(f"{label}: duplicate proposal for task_id '{task_id}'.")
                schema_all_valid = False
                continue
            seen_task_ids.add(task_id)

            schema_ok_items.append(item)

        checks["schema_valid"] = schema_all_valid

        # --- Per-item checks (2.4.2 - 2.4.7) ---
        task_ids_known = True
        actions_allowed = True
        fixed_tasks_unchanged = True
        medication_tasks_unchanged = True
        times_valid = True
        inside_availability = window_ok

        candidate_tasks_by_id = dict(tasks_by_id)
        normalized_items: list[ProposedChange] = []

        for item in schema_ok_items:
            task_id, action = item["task_id"], item["action"]
            original_time, new_time, reason = item["original_time"], item["new_time"], item["reason"]
            label = f"task '{task_id}'"

            snap_task = tasks_by_id.get(task_id)
            if snap_task is None:
                errors.append(f"{label}: unknown task_id (not present in the reviewed schedule).")
                task_ids_known = False
                continue

            if action not in ALLOWED_ACTIONS:
                errors.append(
                    f"{label}: action '{action}' is not allowed. "
                    f"Allowed actions: {', '.join(sorted(ALLOWED_ACTIONS))}."
                )
                actions_allowed = False
                continue

            if action != "move":
                normalized_items.append(ProposedChange(task_id, action, original_time, new_time, reason))
                continue

            normalized_type = (snap_task.task_type or "").strip().lower()
            is_protected = snap_task.flexibility == "fixed" or normalized_type in FIXED_TASK_TYPES
            if is_protected:
                errors.append(f"{label}: cannot move a fixed/protected task ({snap_task.task_type}).")
                fixed_tasks_unchanged = False
                if normalized_type in FIXED_TASK_TYPES:
                    medication_tasks_unchanged = False
                continue

            if original_time != snap_task.preferred_time:
                errors.append(
                    f"{label}: original_time '{original_time}' does not match the reviewed "
                    f"schedule ('{snap_task.preferred_time}') — stale or fabricated proposal."
                )
                times_valid = False
                continue

            valid_new_time = isinstance(new_time, str) and _TIME_PATTERN.match(new_time)
            valid_original_time = isinstance(original_time, str) and _TIME_PATTERN.match(original_time)
            if not (valid_new_time and valid_original_time):
                errors.append(
                    f"{label}: time must be zero-padded 24-hour 'HH:MM' (got new_time={new_time!r})."
                )
                times_valid = False
                continue

            if not window_ok:
                inside_availability = False
                continue

            start_minutes = _time_to_minutes(new_time)
            end_minutes = start_minutes + snap_task.duration_minutes
            if not (avail_start <= start_minutes and end_minutes <= avail_end):
                errors.append(f"{label}: proposed time {new_time} falls outside owner availability.")
                inside_availability = False
                continue

            normalized_items.append(ProposedChange(task_id, action, original_time, new_time, reason))
            candidate_tasks_by_id[task_id] = replace(snap_task, preferred_time=new_time)

        # --- Conflict check on the candidate plan (2.4.8) ---
        # Only flag conflicts introduced BY the proposal, not ones that already
        # existed in the reviewed schedule before any move was applied.
        original_conflicts = {
            frozenset((c.task_id_a, c.task_id_b)) for c in find_conflicts(snapshot.tasks)
        }
        candidate_tasks = tuple(candidate_tasks_by_id[t.task_id] for t in snapshot.tasks)
        candidate_conflicts = find_conflicts(candidate_tasks)
        new_conflicts = [
            c for c in candidate_conflicts
            if frozenset((c.task_id_a, c.task_id_b)) not in original_conflicts
        ]
        for c in new_conflicts:
            errors.append(
                f"Proposed changes would create a new conflict between "
                f"tasks '{c.task_id_a}' and '{c.task_id_b}'."
            )
        no_new_conflicts = not new_conflicts

        checks.update({
            "task_ids_known": task_ids_known,
            "actions_allowed": actions_allowed,
            "fixed_tasks_unchanged": fixed_tasks_unchanged,
            "medication_tasks_unchanged": medication_tasks_unchanged,
            "times_valid": times_valid,
            "inside_availability": inside_availability,
            "no_new_conflicts": no_new_conflicts,
            # Structurally guaranteed by the schema above (2.4.9): the accepted
            # fields have no way to carry duration/pet/type/etc. changes, so
            # this is a readable echo of "no unknown fields were present",
            # not a separate enforcement mechanism.
            "protected_fields_unchanged": schema_all_valid,
        })

        valid = not errors and all(checks.values())
        return ValidationResult(
            valid=valid,
            errors=errors,
            checks=checks,
            normalized_changes=normalized_items if valid else [],
        )
       