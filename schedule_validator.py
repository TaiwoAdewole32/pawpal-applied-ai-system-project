"""Deterministic validator for PawPal Sentinel repair proposals.

This is the trust boundary: it never talks to an AI client, never touches
`Owner`/`Scheduler`/`Task` directly, and never mutates anything. It only
reasons about a `ScheduleSnapshot` (Phase 2.1) and a raw proposed-changes
payload (as an AI would return it), and produces a `ValidationResult` the
caller can act on. The separate Phase 2.5/5.5 apply boundary revalidates,
changes only preferred times, and persists atomically.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from collections.abc import Sequence

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


class PersistenceApplyError(SentinelApplyError):
    """A validated change could not be persisted; live task times were rolled back."""


def _parse_time_string(value: str):
    """Inverse of sentinel_models._task_to_snapshot's preferred_time formatting."""
    return datetime.strptime(value, "%H:%M").time()


def _validate_apply_inputs(
    owner: Owner,
    snapshot_version: object,
    validated_changes: object,
    data_file: object,
) -> tuple[ProposedChange, ...]:
    """Validate the approval boundary before rebuilding or mutating anything."""
    if not isinstance(owner, Owner):
        raise TypeError("owner must be an Owner instance.")
    if not isinstance(snapshot_version, str) or not snapshot_version.strip():
        raise ValueError("snapshot_version must be a non-empty string.")
    if not isinstance(validated_changes, Sequence) or isinstance(
        validated_changes, (str, bytes)
    ):
        raise TypeError("validated_changes must be a sequence of ProposedChange values.")
    normalized = tuple(validated_changes)
    if not normalized:
        raise InvalidProposalError(["At least one validated change is required."])
    for index, change in enumerate(normalized):
        if not isinstance(change, ProposedChange):
            raise TypeError(
                f"validated_changes[{index}] must be a ProposedChange, "
                f"got {type(change).__name__}."
            )
    if not isinstance(data_file, (str, os.PathLike)) or not os.fspath(data_file).strip():
        raise ValueError("data_file must be a non-empty string or path-like value.")
    return normalized


def _owner_payload(owner: Owner) -> dict[str, object]:
    """Build the same JSON shape as Owner.save_to_json without mutating the owner."""
    return {
        "name": owner.name,
        "startTime": owner.startTime.isoformat(),
        "endTime": owner.endTime.isoformat(),
        "preferences": owner.preferences,
        "pets": [pet.to_dict() for pet in owner.pets],
        "tasks": [task.to_dict() for task in owner.scheduler.tasks],
    }


def _atomic_save_owner(owner: Owner, data_file: str | os.PathLike[str]) -> None:
    """Write owner data to a temporary sibling and atomically replace the target.

    The original file is not truncated if serialization or replacement fails.
    This function intentionally performs no live task mutation.
    """
    target = Path(os.fspath(data_file))
    parent = target.parent if str(target.parent) else Path(".")
    temp_name: str | None = None
    try:
        parent.mkdir(parents=True, exist_ok=True)
        payload = _owner_payload(owner)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_name = stream.name
            json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        temp_name = None
    except (OSError, TypeError, ValueError) as exc:
        raise PersistenceApplyError(
            f"Approved changes could not be saved safely ({type(exc).__name__})."
        ) from None
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def apply_approved_changes(
    owner: Owner,
    snapshot_version: str,
    validated_changes: Sequence[ProposedChange],
    data_file: str | os.PathLike[str] = "data.json",
) -> None:
    """Revalidate, atomically apply time-only moves, and persist or roll back.

    This is the only mutation boundary used by Phase 5.5. It rebuilds the
    current snapshot, rejects stale or invalid proposals, prepares every task
    lookup and parsed time before mutation, changes only ``preferredTime``, and
    rolls all live task times back if either mutation or persistence fails.
    """
    normalized_changes = _validate_apply_inputs(
        owner, snapshot_version, validated_changes, data_file
    )

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
        for change in normalized_changes
    ]
    result = ScheduleValidator().validate(current_snapshot, proposal_dicts)
    if not result.valid:
        raise InvalidProposalError(result.errors)

    tasks_by_id = {task.taskId: task for task in owner.scheduler.tasks}
    prepared: list[tuple[object, object, object]] = []
    for change in result.normalized_changes:
        if change.action != "move":
            continue
        task = tasks_by_id.get(change.task_id)
        if task is None:
            raise InvalidProposalError([f"Task '{change.task_id}' no longer exists."])
        if change.new_time is None:
            raise InvalidProposalError(
                [f"Task '{change.task_id}' has no new time for its move action."]
            )
        try:
            parsed_time = _parse_time_string(change.new_time)
        except (TypeError, ValueError):
            raise InvalidProposalError(
                [f"Task '{change.task_id}' has an invalid approved time."]
            ) from None
        prepared.append((task, task.preferredTime, parsed_time))

    try:
        for task, _original_time, new_time in prepared:
            task.updateTask("preferredTime", new_time)
        _atomic_save_owner(owner, data_file)
    except Exception:
        # Direct assignment avoids any custom update hook failing during rollback.
        for task, original_time, _new_time in prepared:
            task.preferredTime = original_time
        raise


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
       