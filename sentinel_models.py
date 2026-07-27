"""Immutable schedule snapshots for PawPal Sentinel.

These models are the trust boundary between an owner's live PawPal+ data
(Owner/Pet/Task, which reference each other in a cycle) and anything that
will later read a schedule for AI review. A snapshot is built once, is
read-only, and intentionally omits owner/task fields (notes, medical/food
details) that the scheduling logic never needs and that should never reach
an external AI call.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from pawpal_system import Owner


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


def _compute_version(availability_start: str, availability_end: str, tasks: tuple[TaskSnapshot, ...]) -> str:
    """Stable hash over everything that affects whether a proposal is safe to apply."""
    fields = [availability_start, availability_end]
    for t in tasks:
        fields.extend([
            t.task_id, t.task_name, t.task_type, str(t.duration_minutes),
            t.priority, t.pet_id, t.preferred_time, t.recurrence,
            t.due_date, t.flexibility,
        ])
    stable_string = "|".join(fields)
    return hashlib.sha256(stable_string.encode("utf-8")).hexdigest()


def build_schedule_snapshot(owner: "Owner") -> ScheduleSnapshot:
    """Build a read-only snapshot of an owner's current schedule for Sentinel review.

    Never mutates the live Owner/Pet/Task graph. Deliberately excludes task
    notes and pet medical/food fields (see PAWPAL_SENTINEL_IMPLEMENTATION_PLAN.md
    Phase 2.7) so there is nothing sensitive in a snapshot for a later AI
    prompt to accidentally include.
    """
    task_snapshots = tuple(
        sorted((_task_to_snapshot(t) for t in owner.scheduler.tasks), key=lambda t: t.task_id)
    )
    unscheduled_ids = tuple(
        sorted(t.taskId for t in owner.scheduler.unscheduledTasks)
    )
    availability_start = owner.startTime.strftime("%H:%M")
    availability_end = owner.endTime.strftime("%H:%M")

    return ScheduleSnapshot(
        owner_name=owner.name,
        availability_start=availability_start,
        availability_end=availability_end,
        tasks=task_snapshots,
        unscheduled_task_ids=unscheduled_ids,
        version=_compute_version(availability_start, availability_end, task_snapshots),
    )
