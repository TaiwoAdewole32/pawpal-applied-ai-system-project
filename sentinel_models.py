"""Immutable and strictly validated models for PawPal Sentinel.

The snapshot models form the privacy and mutation boundary between the live
PawPal+ object graph and the AI workflow. The critic and repair response
models form a second boundary: untrusted model output must be validated here
before application logic can use it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Optional

if TYPE_CHECKING:
    from pawpal_system import Owner


# ---------------------------------------------------------------------------
# Immutable schedule snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    task_name: str
    task_type: str
    duration_minutes: int
    priority: str
    pet_id: str
    pet_name: str
    preferred_time: str  # "HH:MM", 24-hour, zero-padded
    recurrence: str
    due_date: str  # ISO date
    flexibility: str


@dataclass(frozen=True)
class ScheduleSnapshot:
    owner_name: str
    availability_start: str  # "HH:MM"
    availability_end: str  # "HH:MM"
    tasks: tuple[TaskSnapshot, ...]
    unscheduled_task_ids: tuple[str, ...]
    version: str


@dataclass(frozen=True)
class ProposedChange:
    task_id: str
    action: str
    original_time: Optional[str]
    new_time: Optional[str]
    reason: str


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    checks: dict[str, bool]
    normalized_changes: list[ProposedChange]


# ---------------------------------------------------------------------------
# Strict Phase 4.3 AI response models
# ---------------------------------------------------------------------------


class AIResponseValidationError(ValueError):
    """Raised when a critic or repair response violates its required schema."""


class CriticStatus(str, Enum):
    NEEDS_REVISION = "needs_revision"
    NO_CHANGE_NEEDED = "no_change_needed"


class IssueSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IssueType(str, Enum):
    SCHEDULE_CONFLICT = "schedule_conflict"
    FIXED_TASK_CONFLICT = "fixed_task_conflict"
    AVAILABILITY_VIOLATION = "availability_violation"
    UNSCHEDULED_TASK = "unscheduled_task"
    CAPACITY_LIMIT = "capacity_limit"
    CARE_RULE_VIOLATION = "care_rule_violation"


MAX_CRITIC_ISSUES = 20
MAX_TASK_IDS_PER_ISSUE = 20
MAX_RULE_SECTIONS_PER_ISSUE = 3
MAX_SUMMARY_LENGTH = 1_000
MAX_EXPLANATION_LENGTH = 1_000
MAX_REASON_LENGTH = 500

_CRITIC_TOP_LEVEL_FIELDS = frozenset({"status", "summary", "issues", "confidence"})
_CRITIC_ISSUE_FIELDS = frozenset(
    {"issue_type", "task_ids", "severity", "explanation", "rule_sections"}
)
_REPAIR_TOP_LEVEL_FIELDS = frozenset({"proposed_changes", "summary"})
_REPAIR_CHANGE_FIELDS = frozenset(
    {"task_id", "action", "original_time", "new_time", "reason"}
)


def _type_name(value: object) -> str:
    return type(value).__name__


def _require_object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AIResponseValidationError(
            f"{label} must be a JSON object, got {_type_name(value)}."
        )
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], label: str
) -> None:
    actual = set(value.keys())
    missing = expected - actual
    extra = actual - expected
    if missing:
        raise AIResponseValidationError(
            f"{label} is missing required field(s): "
            f"{', '.join(sorted(missing))}."
        )
    if extra:
        extra_text = ", ".join(sorted(str(key) for key in extra))
        raise AIResponseValidationError(
            f"{label} contains unknown field(s): {extra_text}."
        )


def _require_text(
    value: object,
    label: str,
    *,
    max_length: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise AIResponseValidationError(
            f"{label} must be a string, got {_type_name(value)}."
        )
    if not allow_empty and not value.strip():
        raise AIResponseValidationError(f"{label} must not be empty.")
    if len(value) > max_length:
        raise AIResponseValidationError(
            f"{label} exceeds the maximum length of {max_length} characters."
        )
    return value.strip()


def _require_string_list(
    value: object,
    label: str,
    *,
    max_items: int,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AIResponseValidationError(
            f"{label} must be a list, got {_type_name(value)}."
        )
    if not allow_empty and not value:
        raise AIResponseValidationError(f"{label} must contain at least one item.")
    if len(value) > max_items:
        raise AIResponseValidationError(
            f"{label} contains {len(value)} items; maximum is {max_items}."
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        text = _require_text(
            item,
            f"{label}[{index}]",
            max_length=200,
        )
        if text in seen:
            raise AIResponseValidationError(
                f"{label} contains duplicate value '{text}'."
            )
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _normalized_known_values(values: Optional[Iterable[str]]) -> Optional[set[str]]:
    if values is None:
        return None
    return {value for value in values if isinstance(value, str) and value}


@dataclass(frozen=True)
class CriticIssue:
    issue_type: IssueType
    task_ids: tuple[str, ...]
    severity: IssueSeverity
    explanation: str
    rule_sections: tuple[str, ...]

    @classmethod
    def from_dict(
        cls,
        payload: object,
        *,
        known_task_ids: Optional[Iterable[str]] = None,
        known_rule_sections: Optional[Iterable[str]] = None,
        label: str = "critic issue",
    ) -> "CriticIssue":
        obj = _require_object(payload, label)
        _require_exact_keys(obj, _CRITIC_ISSUE_FIELDS, label)

        issue_type_raw = _require_text(
            obj["issue_type"],
            f"{label}.issue_type",
            max_length=100,
        )
        try:
            issue_type = IssueType(issue_type_raw)
        except ValueError:
            allowed = ", ".join(item.value for item in IssueType)
            raise AIResponseValidationError(
                f"{label}.issue_type '{issue_type_raw}' is not allowed. "
                f"Allowed values: {allowed}."
            ) from None

        severity_raw = _require_text(
            obj["severity"],
            f"{label}.severity",
            max_length=20,
        )
        try:
            severity = IssueSeverity(severity_raw)
        except ValueError:
            allowed = ", ".join(item.value for item in IssueSeverity)
            raise AIResponseValidationError(
                f"{label}.severity '{severity_raw}' is not allowed. "
                f"Allowed values: {allowed}."
            ) from None

        task_ids = _require_string_list(
            obj["task_ids"],
            f"{label}.task_ids",
            max_items=MAX_TASK_IDS_PER_ISSUE,
            allow_empty=False,
        )
        known_tasks = _normalized_known_values(known_task_ids)
        if known_tasks is not None:
            unknown = [task_id for task_id in task_ids if task_id not in known_tasks]
            if unknown:
                raise AIResponseValidationError(
                    f"{label}.task_ids contains unknown task ID(s): "
                    f"{', '.join(unknown)}."
                )

        explanation = _require_text(
            obj["explanation"],
            f"{label}.explanation",
            max_length=MAX_EXPLANATION_LENGTH,
        )
        rule_sections = _require_string_list(
            obj["rule_sections"],
            f"{label}.rule_sections",
            max_items=MAX_RULE_SECTIONS_PER_ISSUE,
            allow_empty=True,
        )
        known_rules = _normalized_known_values(known_rule_sections)
        if known_rules is not None:
            unknown_rules = [
                section for section in rule_sections if section not in known_rules
            ]
            if unknown_rules:
                raise AIResponseValidationError(
                    f"{label}.rule_sections contains a section that was not retrieved: "
                    f"{', '.join(unknown_rules)}."
                )

        return cls(
            issue_type=issue_type,
            task_ids=task_ids,
            severity=severity,
            explanation=explanation,
            rule_sections=rule_sections,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "issue_type": self.issue_type.value,
            "task_ids": list(self.task_ids),
            "severity": self.severity.value,
            "explanation": self.explanation,
            "rule_sections": list(self.rule_sections),
        }


@dataclass(frozen=True)
class CriticResult:
    status: CriticStatus
    summary: str
    issues: tuple[CriticIssue, ...]
    confidence: float

    @classmethod
    def from_dict(
        cls,
        payload: object,
        *,
        known_task_ids: Optional[Iterable[str]] = None,
        known_rule_sections: Optional[Iterable[str]] = None,
    ) -> "CriticResult":
        obj = _require_object(payload, "critic response")
        _require_exact_keys(obj, _CRITIC_TOP_LEVEL_FIELDS, "critic response")

        status_raw = _require_text(
            obj["status"], "critic response.status", max_length=50
        )
        try:
            status = CriticStatus(status_raw)
        except ValueError:
            allowed = ", ".join(item.value for item in CriticStatus)
            raise AIResponseValidationError(
                f"critic response.status '{status_raw}' is not allowed. "
                f"Allowed values: {allowed}."
            ) from None

        summary = _require_text(
            obj["summary"],
            "critic response.summary",
            max_length=MAX_SUMMARY_LENGTH,
        )

        raw_issues = obj["issues"]
        if not isinstance(raw_issues, list):
            raise AIResponseValidationError(
                f"critic response.issues must be a list, got {_type_name(raw_issues)}."
            )
        if len(raw_issues) > MAX_CRITIC_ISSUES:
            raise AIResponseValidationError(
                f"critic response.issues contains {len(raw_issues)} items; "
                f"maximum is {MAX_CRITIC_ISSUES}."
            )

        issues = tuple(
            CriticIssue.from_dict(
                issue,
                known_task_ids=known_task_ids,
                known_rule_sections=known_rule_sections,
                label=f"critic response.issues[{index}]",
            )
            for index, issue in enumerate(raw_issues)
        )

        confidence_raw = obj["confidence"]
        if isinstance(confidence_raw, bool) or not isinstance(
            confidence_raw, (int, float)
        ):
            raise AIResponseValidationError(
                "critic response.confidence must be a number between 0 and 1; "
                f"got {_type_name(confidence_raw)}."
            )
        confidence = float(confidence_raw)
        if not 0.0 <= confidence <= 1.0:
            raise AIResponseValidationError(
                "critic response.confidence must be between 0 and 1."
            )

        if status is CriticStatus.NEEDS_REVISION and not issues:
            raise AIResponseValidationError(
                "critic response with status 'needs_revision' must include at least one issue."
            )
        if status is CriticStatus.NO_CHANGE_NEEDED and issues:
            raise AIResponseValidationError(
                "critic response with status 'no_change_needed' must have an empty issues list."
            )

        return cls(
            status=status,
            summary=summary,
            issues=issues,
            confidence=confidence,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "summary": self.summary,
            "issues": [issue.to_dict() for issue in self.issues],
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class RepairResult:
    proposed_changes: tuple[ProposedChange, ...]
    summary: str

    @classmethod
    def from_dict(
        cls,
        payload: object,
        *,
        max_changes: int,
    ) -> "RepairResult":
        if isinstance(max_changes, bool) or not isinstance(max_changes, int):
            raise TypeError("max_changes must be an integer.")
        if max_changes < 0:
            raise ValueError("max_changes must not be negative.")

        obj = _require_object(payload, "repair response")
        _require_exact_keys(obj, _REPAIR_TOP_LEVEL_FIELDS, "repair response")

        summary = _require_text(
            obj["summary"],
            "repair response.summary",
            max_length=MAX_SUMMARY_LENGTH,
        )
        raw_changes = obj["proposed_changes"]
        if not isinstance(raw_changes, list):
            raise AIResponseValidationError(
                "repair response.proposed_changes must be a list, "
                f"got {_type_name(raw_changes)}."
            )
        if len(raw_changes) > max_changes:
            raise AIResponseValidationError(
                f"repair response proposes {len(raw_changes)} changes for only "
                f"{max_changes} reviewed task(s)."
            )

        changes: list[ProposedChange] = []
        for index, raw_change in enumerate(raw_changes):
            label = f"repair response.proposed_changes[{index}]"
            change = _require_object(raw_change, label)
            _require_exact_keys(change, _REPAIR_CHANGE_FIELDS, label)

            task_id = _require_text(
                change["task_id"], f"{label}.task_id", max_length=200
            )
            action = _require_text(
                change["action"], f"{label}.action", max_length=100
            )
            reason = _require_text(
                change["reason"],
                f"{label}.reason",
                max_length=MAX_REASON_LENGTH,
            )

            original_time = change["original_time"]
            if original_time is not None and not isinstance(original_time, str):
                raise AIResponseValidationError(
                    f"{label}.original_time must be a string or null, "
                    f"got {_type_name(original_time)}."
                )
            new_time = change["new_time"]
            if new_time is not None and not isinstance(new_time, str):
                raise AIResponseValidationError(
                    f"{label}.new_time must be a string or null, "
                    f"got {_type_name(new_time)}."
                )

            changes.append(
                ProposedChange(
                    task_id=task_id,
                    action=action,
                    original_time=original_time,
                    new_time=new_time,
                    reason=reason,
                )
            )

        return cls(proposed_changes=tuple(changes), summary=summary)

    def to_dict(self) -> dict[str, object]:
        return {
            "proposed_changes": [
                {
                    "task_id": change.task_id,
                    "action": change.action,
                    "original_time": change.original_time,
                    "new_time": change.new_time,
                    "reason": change.reason,
                }
                for change in self.proposed_changes
            ],
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Explicit serialization helpers for AI payload minimization
# ---------------------------------------------------------------------------


def task_snapshot_to_dict(task: TaskSnapshot) -> dict[str, object]:
    """Serialize only the allowlisted TaskSnapshot fields."""
    if not isinstance(task, TaskSnapshot):
        raise TypeError("task must be a TaskSnapshot.")
    return {
        "task_id": task.task_id,
        "task_name": task.task_name,
        "task_type": task.task_type,
        "duration_minutes": task.duration_minutes,
        "priority": task.priority,
        "pet_id": task.pet_id,
        "pet_name": task.pet_name,
        "preferred_time": task.preferred_time,
        "recurrence": task.recurrence,
        "due_date": task.due_date,
        "flexibility": task.flexibility,
    }


def schedule_snapshot_to_dict(snapshot: ScheduleSnapshot) -> dict[str, object]:
    """Serialize a schedule without notes, pet medical data, or live objects."""
    if not isinstance(snapshot, ScheduleSnapshot):
        raise TypeError("snapshot must be a ScheduleSnapshot.")
    return {
        "owner_name": snapshot.owner_name,
        "availability_start": snapshot.availability_start,
        "availability_end": snapshot.availability_end,
        "tasks": [task_snapshot_to_dict(task) for task in snapshot.tasks],
        "unscheduled_task_ids": list(snapshot.unscheduled_task_ids),
        "version": snapshot.version,
    }


# ---------------------------------------------------------------------------
# Snapshot construction
# ---------------------------------------------------------------------------


def _task_to_snapshot(task) -> TaskSnapshot:
    return TaskSnapshot(
        task_id=task.taskId,
        task_name=task.taskName,
        task_type=task.taskType,
        duration_minutes=task.durationMinutes,
        priority=task.priority.value,
        pet_id=task.pet.petId,
        pet_name=task.pet.name,
        preferred_time=task.preferredTime.strftime("%H:%M"),
        recurrence=task.recurrence,
        due_date=task.dueDate.isoformat(),
        flexibility=task.flexibility.value,
    )


def _compute_version(
    availability_start: str,
    availability_end: str,
    tasks: tuple[TaskSnapshot, ...],
) -> str:
    """Stable hash over everything that affects whether a proposal is safe."""
    fields = [availability_start, availability_end]
    for task in tasks:
        fields.extend(
            [
                task.task_id,
                task.task_name,
                task.task_type,
                str(task.duration_minutes),
                task.priority,
                task.pet_id,
                task.preferred_time,
                task.recurrence,
                task.due_date,
                task.flexibility,
            ]
        )
    stable_string = "|".join(fields)
    return hashlib.sha256(stable_string.encode("utf-8")).hexdigest()


def build_schedule_snapshot(owner: "Owner") -> ScheduleSnapshot:
    """Build a read-only snapshot of an owner's current schedule.

    Notes and pet medical/food fields are deliberately excluded. Completed
    tasks are historical and are also excluded from AI review.
    """
    task_snapshots = tuple(
        sorted(
            (_task_to_snapshot(task) for task in owner.scheduler.tasks if not task.completed),
            key=lambda task: task.task_id,
        )
    )
    unscheduled_ids = tuple(
        sorted(task.taskId for task in owner.scheduler.unscheduledTasks)
    )
    availability_start = owner.startTime.strftime("%H:%M")
    availability_end = owner.endTime.strftime("%H:%M")

    return ScheduleSnapshot(
        owner_name=owner.name,
        availability_start=availability_start,
        availability_end=availability_end,
        tasks=task_snapshots,
        unscheduled_task_ids=unscheduled_ids,
        version=_compute_version(
            availability_start, availability_end, task_snapshots
        ),
    )

