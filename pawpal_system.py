from __future__ import annotations
from dataclasses import dataclass, field as dc_field
from datetime import time as Time, datetime, timedelta, date as Date
from enum import Enum
from typing import Any, ClassVar, Optional
import json
import os
import uuid


class Priority(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"

class Flexibility(str, Enum):
    FIXED = "fixed"
    PREFERRED = "preferred"
    FLEXIBLE = "flexible"


FIXED_TASK_TYPES = {
    "medication",
    "vet",
    "veterinarian",
    "vet appointment",
    "veterinarian appointment",
    "appointment",
}
PREFERRED_TASK_TYPES = {"feed", "feeding", "meal"}


def resolve_flexibility(task_type: str, requested: "str | Flexibility | None") -> Flexibility:
    """Determine the effective flexibility for a task type, enforcing protected-type rules."""
    normalized_type = (task_type or "").strip().lower()

    if requested is None:
        if normalized_type in FIXED_TASK_TYPES:
            value = Flexibility.FIXED
        elif normalized_type in PREFERRED_TASK_TYPES:
            value = Flexibility.PREFERRED
        else:
            value = Flexibility.FLEXIBLE
    elif isinstance(requested, Flexibility):
        value = requested
    elif isinstance(requested, str):
        try:
            value = Flexibility(requested)
        except ValueError:
            raise ValueError(
                f"Invalid flexibility '{requested}'. Allowed values: fixed, preferred, flexible."
            )
    else:
        raise TypeError(
            f"flexibility must be a string or Flexibility, got {type(requested).__name__}."
        )

    # Protected task types can never be weakened below fixed, regardless of what was requested.
    if normalized_type in FIXED_TASK_TYPES:
        return Flexibility.FIXED
    return value


def format_time(t: Time) -> str:
    """Format a time as 12-hour clock with AM/PM, no leading zero (e.g. '5:00 PM')."""
    return t.strftime("%I:%M %p").lstrip("0")


def format_duration(total_minutes: int) -> str:
    """Format minutes as 'X hours and Y minutes' with correct singular/plural."""
    hours, minutes = divmod(int(total_minutes), 60)
    hour_str = f"{hours} hour{'s' if hours != 1 else ''}"
    minute_str = f"{minutes} minute{'s' if minutes != 1 else ''}"
    if hours and minutes:
        return f"{hour_str} and {minute_str}"
    return hour_str if hours else minute_str


@dataclass
class Pet:

    EDITABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "name",
        "species",
        "breed",
        "age",
        "foodType",
        "medication",
        "energyLevel",
        "careNeeds",
    })
    
    name: str
    species: str
    breed: str
    age: int
    foodType: str
    medication: str
    energyLevel: int
    careNeeds: list[str] = dc_field(default_factory=list) # Gives every pet its own list
    tasks: list[Task] = dc_field(default_factory=list) # Gives every pet its own list
    petId: str = dc_field(default_factory=lambda: str(uuid.uuid4()))

    def __eq__(self, other: object) -> bool:
        # Pet.tasks and Task.pet reference each other, so the default dataclass
        # field-by-field __eq__ recurses forever when comparing two non-identical
        # object graphs (e.g. Streamlit deep-copying a widget's Pet value to diff
        # across reruns). Identity by petId sidesteps the cycle entirely.
        return isinstance(other, Pet) and self.petId == other.petId

    def __hash__(self) -> int:
        return hash(self.petId)

    def addCareNeed(self, need: str) -> None:
        """Append a care need string to this pet's careNeeds list."""
        self.careNeeds.append(need)

    def updatePet(self, field_name: str, value: Any) -> None:
        """Update a single pet field by name if it exists on the pet."""
        if field_name not in self.EDITABLE_FIELDS:
            allowed = ", ".join(sorted(self.EDITABLE_FIELDS))
            raise ValueError(
                f"Pet field '{field_name}' is not editable. "
                f"Allowed fields: {allowed}."
            )
        
        setattr(self, field_name, value)

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict of this pet's fields (tasks excluded; Scheduler owns that linkage)."""
        return {
            "petId": self.petId,
            "name": self.name,
            "species": self.species,
            "breed": self.breed,
            "age": self.age,
            "foodType": self.foodType,
            "medication": self.medication,
            "energyLevel": self.energyLevel,
            "careNeeds": list(self.careNeeds),
        }

    @staticmethod
    def from_dict(data: dict) -> Pet:
        """Rebuild a Pet from a dict produced by to_dict()."""
        return Pet(
            petId=data.get("petId", str(uuid.uuid4())),
            name=data["name"],
            species=data["species"],
            breed=data["breed"],
            age=data["age"],
            foodType=data["foodType"],
            medication=data["medication"],
            energyLevel=data["energyLevel"],
            careNeeds=list(data.get("careNeeds", [])),
        )

    def getPetSummary(self) -> str:
        """Return a single-line summary of the pet's key attributes."""
        meds = self.medication or "none"
        needs = ", ".join(self.careNeeds) if self.careNeeds else "none"
        return (
            f"{self.name} ({self.breed}, {self.species}) | "
            f"Age: {self.age} | Food: {self.foodType} | "
            f"Medication: {meds} | Energy: {self.energyLevel}/10 | "
            f"Care needs: {needs}"
        )


@dataclass
class Task:
    """A care task with a deliberately restricted public update API."""

    EDITABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "taskName",
        "taskType",
        "durationMinutes",
        "priority",
        "preferredTime",
        "flexibility",
        "recurrence",
        "dueDate",
        "notes",
    })

    taskName: str
    taskType: str
    durationMinutes: int
    priority: Priority
    pet: Pet
    preferredTime: Time          # datetime.time replaces the bare "HH:MM" string
    flexibility: Optional[Flexibility] = None  # resolved in __post_init__; None means "use the type-based default"
    recurrence: str = "none"     # "none" | "daily" | "weekly"
    dueDate: Date = dc_field(default_factory=lambda: datetime.today().date())
    completed: bool = False
    notes: str = ""
    taskId: str = dc_field(default_factory=lambda: str(uuid.uuid4()))

    def __eq__(self, other: object) -> bool:
        # Same cyclic-reference reasoning as Pet.__eq__ (Task.pet <-> Pet.tasks):
        # compare by the existing taskId instead of recursing through every field.
        return isinstance(other, Task) and self.taskId == other.taskId

    def __hash__(self) -> int:
        return hash(self.taskId)

    def __post_init__(self) -> None:
        self.flexibility = resolve_flexibility(self.taskType, self.flexibility)

    def markComplete(self) -> None:
        """Mark this task as completed by setting completed to True."""
        self.completed = True

    def updateTask(self, field_name: str, value: int) -> None:
        """Update a single task field by name if it exists on the task."""
        if field_name not in self.EDITABLE_FIELDS:
            allowed = ", ".join(sorted(self.EDITABLE_FIELDS))
            raise ValueError(
                f"Task field '{field_name}' is not editable. "
                f"Allowed fields: {allowed}."
            )

        if field_name == "flexibility":
            value = resolve_flexibility(self.taskType, value)

        setattr(self, field_name, value)

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict of this task, referencing its pet by petId (not nested)."""
        return {
            "taskId": self.taskId,
            "taskName": self.taskName,
            "taskType": self.taskType,
            "durationMinutes": self.durationMinutes,
            "priority": self.priority.value,
            "petId": self.pet.petId,
            "preferredTime": self.preferredTime.isoformat(),
            "recurrence": self.recurrence,
            "dueDate": self.dueDate.isoformat(),
            "completed": self.completed,
            "notes": self.notes,
            "flexibility": self.flexibility.value,
        }

    @staticmethod
    def from_dict(data: dict, pet: Pet) -> Task:
        """Rebuild a Task from a dict produced by to_dict(), linked to the given (already-resolved) pet."""
        return Task(
            taskId=data.get("taskId", str(uuid.uuid4())),
            taskName=data["taskName"],
            taskType=data["taskType"],
            durationMinutes=data["durationMinutes"],
            priority=Priority(data["priority"]),
            pet=pet,
            preferredTime=Time.fromisoformat(data["preferredTime"]),
            recurrence=data.get("recurrence", "none"),
            dueDate=Date.fromisoformat(data["dueDate"]),
            completed=data.get("completed", False),
            notes=data.get("notes", ""),
            flexibility=data.get("flexibility"),
        )

    _RECURRENCE_DELTAS = {
        "daily": timedelta(days=1),
        "weekly": timedelta(weeks=1),
    }

    def _spawn_next(self) -> Optional[Task]: #Meant to be a helper method
        """Return a fresh Task for the next recurrence, or None if this task is non-recurring or unrecognized."""
        delta = self._RECURRENCE_DELTAS.get(self.recurrence)
        if delta is None:
            return None
        return Task(
            #Creates a brand new task with the same attributes as the current one, but with a new due date
            taskName=self.taskName,
            taskType=self.taskType,
            durationMinutes=self.durationMinutes,
            priority=self.priority,
            pet=self.pet,
            preferredTime=self.preferredTime,
            recurrence=self.recurrence,
            dueDate=self.dueDate + delta,
            notes=self.notes,
            flexibility=self.flexibility,
        )

    def getTaskSummary(self) -> str:
        """Return a single-line summary of the task including priority, timing, recurrence, and status."""
        status = "done" if self.completed else "pending"
        recurrence_text = f" ({self.recurrence})" if self.recurrence != "none" else "" #If task repeats, add text like (daily) or (weekly) to the summary
        summary = (
            f"[{self.priority.value.upper()}] {self.taskName} ({self.taskType}) | "
            f"{self.durationMinutes} min @ {format_time(self.preferredTime)} | "
            f"Pet: {self.pet.name} | Due: {self.dueDate}{recurrence_text} | Status: {status}"
        )
        if self.notes:
            summary += f" | Notes: {self.notes}"
        summary += f" | Flexibility: {self.flexibility.value}"
        return summary


class Scheduler:
    _PRIORITY_ORDER = {
        Priority.HIGH: 3,
        Priority.MEDIUM: 2,
        Priority.LOW: 1
    }

    def __init__(
        self,
        tasks: Optional[list[Task]] = None,
        timeAvailable: int = 0,
        startTime: Optional[Time] = None,
        dailyPlan: Optional[list[Task]] = None,
        unscheduledTasks: Optional[list[Task]] = None,
        ownerName: str = "",
    ) -> None:
        self.tasks: list[Task] = tasks if tasks is not None else []
        self.timeAvailable = timeAvailable
        self.startTime = startTime
        self.dailyPlan: list[Task] = dailyPlan if dailyPlan is not None else []
        self.unscheduledTasks: list[Task] = (
            unscheduledTasks if unscheduledTasks is not None else []
        )
        self.ownerName = ownerName
        self.planGenerated = False

    def addTask(self, task: Task) -> None:
        """Add a task to the scheduler and register it on the associated pet."""
        self.tasks.append(task)
        if task not in task.pet.tasks:
            task.pet.tasks.append(task)

    def editTask(self, task: Task) -> None:
        """Replace the stored task matching task.taskId in both the scheduler and the pet."""
        for i, stored_task in enumerate(self.tasks):
            #i = position number, stored_task = actual task
            if stored_task.taskId == task.taskId:
                self.tasks[i] = task
                for j, pet_task in enumerate(task.pet.tasks):
                    if pet_task.taskId == task.taskId:
                        task.pet.tasks[j] = task
                        break
                return

    def removeTask(self, task: Task) -> None:
        """Remove a task from the scheduler, its pet, and any cached plan lists."""
        if task in self.tasks:
            self.tasks.remove(task)
        if task in task.pet.tasks:
            task.pet.tasks.remove(task)
        if task in self.dailyPlan:
            self.dailyPlan.remove(task)
        if task in self.unscheduledTasks:
            self.unscheduledTasks.remove(task)

    def generatePlan(self) -> list[Task]:
        """Fit today's pending tasks by priority within the available time window."""
        self.planGenerated = True
        today = datetime.today()
        #Creates a listt of tasks that are allowed to be scheduled
        eligible = [t for t in self.tasks if not t.completed and t.dueDate <= today.date()]
        sorted_tasks = self.sortTasksByPriority(eligible)
        self.dailyPlan = []
        self.unscheduledTasks = []
        remaining = self.timeAvailable
        self.dailyPlan = self.sort_by_time(self.dailyPlan)

        window_end: Optional[datetime] = None
        #If the owner has a start time, calculate the end of the available window (may roll past midnight)
        if self.startTime is not None:
            window_end = (
                datetime.combine(today, self.startTime)
                + timedelta(minutes=self.timeAvailable)
            )

        for task in sorted_tasks:
            in_window = (
                self._fits_window(task, today, window_end)
                if self.startTime is not None
                else True
            )
            if task.durationMinutes <= remaining and in_window:
                self.dailyPlan.append(task)
                remaining -= task.durationMinutes
            else:
                self.unscheduledTasks.append(task)

        self.dailyPlan = self.sort_by_time(self.dailyPlan)
        return self.dailyPlan

    def _fits_window(self, task: Task, today_dt: datetime, window_end: datetime) -> bool:
        """Return True if the task's time slot falls entirely within the owner's window (window may span midnight)."""
        window_start = datetime.combine(today_dt, self.startTime)
        task_start = datetime.combine(today_dt, task.preferredTime)
        if task_start < window_start:
            # Window wraps past midnight; treat early-morning times as the window's next-day portion
            task_start += timedelta(days=1)
        task_end = task_start + timedelta(minutes=task.durationMinutes)
        return task_start >= window_start and task_end <= window_end

    def sortTasksByPriority(self, tasks: Optional[list[Task]] = None) -> list[Task]:
        """Return tasks sorted by priority first, then by preferred time."""
        task_list = tasks if tasks is not None else self.tasks
        return sorted(
            task_list,
            key=lambda task: (
                -self._PRIORITY_ORDER.get(task.priority, 0),
                task.preferredTime,
            ),
    )
    def sort_by_time(self, tasks: Optional[list[Task]] = None) -> list[Task]:
        """Return all tasks sorted from earliest to latest preferred time."""
        task_list = tasks if tasks is not None else self.tasks
        return sorted(task_list, key=lambda task: task.preferredTime)

    def filterTasks(
        self,
        completed: Optional[bool] = None,
        petName: Optional[str] = None,
    ) -> list[Task]:
        """Return tasks matching the given completion status and/or pet name; both filters are optional and combinable."""
        result = self.tasks
        if completed is not None:
            result = [t for t in result if t.completed == completed]
        if petName is not None:
            result = [t for t in result if t.pet.name.lower() == petName.lower()]
        return result

    def completeTask(self, task: Task) -> Optional[Task]:
        """Mark a task complete and auto-register its next occurrence if it is recurring."""
        if task.completed:
            return None
        task.markComplete()
        next_task = task._spawn_next()
        if next_task is not None:
            self.addTask(next_task)
        return next_task

    def detectConflicts(self) -> list[Task]:
        """Return all tasks whose time windows overlap with at least one other task."""
        conflicts: dict[str, Task] = {}
        today = datetime.today()

        for i, task_a in enumerate(self.tasks):
            if task_a.completed:
                continue
            start_a = datetime.combine(today, task_a.preferredTime)
            end_a = start_a + timedelta(minutes=task_a.durationMinutes)

            for task_b in self.tasks[i + 1:]:
                # Compare task_a only with tasks after it
                if task_b.completed:
                    continue
                start_b = datetime.combine(today, task_b.preferredTime)
                end_b = start_b + timedelta(minutes=task_b.durationMinutes)

                #If Task A starts before Task B ends and Task B starts before Task A ends, then they overlap
                if start_a < end_b and start_b < end_a:
                    conflicts[task_a.taskId] = task_a
                    conflicts[task_b.taskId] = task_b

        return list(conflicts.values())

    def getConflictWarnings(self) -> list[str]:
        """Return a warning string for every overlapping task pair, noting whether it is same-pet or cross-pet."""
        warnings: list[str] = []
        today = datetime.today()

        for i, task_a in enumerate(self.tasks):
            if task_a.completed:
                continue
            start_a = datetime.combine(today, task_a.preferredTime)
            end_a   = start_a + timedelta(minutes=task_a.durationMinutes)

            for task_b in self.tasks[i + 1:]:
                if task_b.completed:
                    continue
                start_b = datetime.combine(today, task_b.preferredTime)
                end_b   = start_b + timedelta(minutes=task_b.durationMinutes)

                if start_a < end_b and start_b < end_a:
                    conflict_type = (
                        "same-pet" if task_a.pet.name == task_b.pet.name else "cross-pet"
                    )
                    warnings.append(
                        f"WARNING [{conflict_type}] "
                        f"'{task_a.taskName}' ({task_a.pet.name}, "
                        f"{format_time(start_a.time())}–{format_time(end_a.time())}) "
                        f"overlaps with "
                        f"'{task_b.taskName}' ({task_b.pet.name}, "
                        f"{format_time(start_b.time())}–{format_time(end_b.time())})"
                    )

        return warnings

    def explainPlan(self) -> str:
        """Return a daily plan grouped by pet, including unscheduled tasks."""
        if not self.planGenerated:
            return "No plan has been generated yet. Click \"Generate Schedule\" to create one."

        total_minutes = sum(task.durationMinutes for task in self.dailyPlan)

        if self.ownerName:
            heading = f"Daily plan for {self.ownerName}'s pets"
        else:
            heading = "Daily plan"

        if not self.dailyPlan:
            lines = [f"{heading} — no tasks were scheduled today."]
        else:
            lines = [
                f"{heading} — {len(self.dailyPlan)} task(s), "
                f"{format_duration(total_minutes)} of {format_duration(self.timeAvailable)} used:"
            ]

        tasks_by_pet: dict[str, list[Task]] = {}
        pet_labels: dict[str, str] = {}

        for task in self.dailyPlan:
            pet_key = task.pet.name
            tasks_by_pet.setdefault(pet_key, []).append(task)
            if task.pet.breed:
                pet_labels[pet_key] = f"{task.pet.name} ({task.pet.breed})"
            else:
                pet_labels[pet_key] = f"{task.pet.name} ({task.pet.species})"

        for pet_key, pet_tasks in tasks_by_pet.items():
            lines.append(f"\n{pet_labels[pet_key]}:")
            for task in sorted(pet_tasks, key=lambda t: t.preferredTime):
                lines.append(
                    f"  {format_time(task.preferredTime)} -> {task.taskName} "
                    f"({task.durationMinutes} min) "
                    f"[priority: {task.priority.value}]"
                )

        if self.unscheduledTasks:
            lines.append(
                f"\nNot scheduled "
                f"({len(self.unscheduledTasks)} task(s) — insufficient time or outside window):"
            )
            for task in self.unscheduledTasks:
                lines.append(
                    f"  - {task.taskName} for {task.pet.name} "
                    f"({task.durationMinutes} min) "
                    f"[priority: {task.priority.value}]"
                )

        return "\n".join(lines)


class Owner:
    def __init__(
        self,
        name: str,
        startTime: Time,
        endTime: Time,
        preferences: dict[str, str],
        pets: Optional[list[Pet]] = None,
        scheduler: Optional[Scheduler] = None,
    ) -> None:
        self.name = name
        self.startTime = startTime
        self.endTime = endTime
        self.preferences = preferences
        self.pets: list[Pet] = pets if pets is not None else []
        self.scheduler: Scheduler = (
            scheduler if scheduler is not None
            else Scheduler(
                timeAvailable=self.availableMinutes,
                startTime=startTime,
                ownerName=name,
            )
        )

    @property
    def availableMinutes(self) -> int:
        """Compute available minutes from the owner's start-to-end time window."""
        start = datetime.combine(datetime.today(), self.startTime)
        end = datetime.combine(datetime.today(), self.endTime)
        return max(0, int((end - start).total_seconds() // 60))

    def addPet(self, pet: Pet) -> None:
        """Add a pet to this owner's list of pets."""
        self.pets.append(pet)

    def removePet(self, pet: Pet) -> None:
        """Remove a pet from this owner and drop any of its tasks from the scheduler."""
        if pet in self.pets:
            self.pets.remove(pet)
        self.scheduler.tasks = [t for t in self.scheduler.tasks if t.pet is not pet]

    def updatePreferences(self, prefs: dict[str, str]) -> None:
        """Merge the given preferences into the owner's existing preferences."""
        self.preferences.update(prefs)

    def getAvailableTime(self) -> int:
        """Return the number of minutes the owner has available for pet care."""
        return self.availableMinutes

    def save_to_json(self, filepath: str = "data.json") -> None:
        """Persist this owner, its pets, and its scheduler's tasks to a JSON file."""
        data = {
            "name": self.name,
            "startTime": self.startTime.isoformat(),
            "endTime": self.endTime.isoformat(),
            "preferences": self.preferences,
            "pets": [pet.to_dict() for pet in self.pets],
            "tasks": [task.to_dict() for task in self.scheduler.tasks],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def load_from_json(filepath: str = "data.json") -> Optional[Owner]:
        """Rebuild an Owner (with pets and tasks) from a JSON file, or None if it's missing/unreadable."""
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

        owner = Owner(
            name=data["name"],
            startTime=Time.fromisoformat(data["startTime"]),
            endTime=Time.fromisoformat(data["endTime"]),
            preferences=data.get("preferences", {}),
        )

        pets_by_id: dict[str, Pet] = {}
        for pet_data in data.get("pets", []):
            pet = Pet.from_dict(pet_data)
            owner.addPet(pet)
            pets_by_id[pet.petId] = pet

        for task_data in data.get("tasks", []):
            pet = pets_by_id.get(task_data["petId"])
            if pet is None:
                continue
            owner.scheduler.addTask(Task.from_dict(task_data, pet))

        return owner
