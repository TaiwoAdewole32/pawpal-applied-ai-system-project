from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from pawpal_system import Flexibility, Owner, Pet, Priority, Task
from sentinel_models import build_schedule_snapshot, task_snapshot_to_dict
from sentinel_service import ApprovalStatus, PawPalSentinel, WorkflowStatus


@dataclass(frozen=True)
class ProfileCase:
    owner_name: str
    start: time
    end: time
    pet_name: str
    species: str
    breed: str
    age: int
    food_type: str
    medication: str
    care_task_type: str
    requested_flexibility: Flexibility
    expected_flexibility: Flexibility


PROFILE_CASES = (
    ProfileCase(
        owner_name="Jordan",
        start=time(7, 0),
        end=time(19, 0),
        pet_name="Mochi",
        species="Dog",
        breed="Golden Retriever",
        age=5,
        food_type="Dry Kibble",
        medication="none",
        care_task_type="walk",
        requested_flexibility=Flexibility.FLEXIBLE,
        expected_flexibility=Flexibility.FLEXIBLE,
    ),
    ProfileCase(
        owner_name="Maya",
        start=time(6, 0),
        end=time(14, 0),
        pet_name="Luna",
        species="Cat",
        breed="Siamese",
        age=12,
        food_type="Wet Food",
        medication="insulin",
        care_task_type="feeding",
        requested_flexibility=Flexibility.PREFERRED,
        expected_flexibility=Flexibility.PREFERRED,
    ),
    ProfileCase(
        owner_name="Andre",
        start=time(9, 0),
        end=time(17, 0),
        pet_name="Clover",
        species="Rabbit",
        breed="Holland Lop",
        age=3,
        food_type="Timothy Hay",
        medication="none",
        care_task_type="grooming",
        requested_flexibility=Flexibility.FLEXIBLE,
        expected_flexibility=Flexibility.FLEXIBLE,
    ),
    ProfileCase(
        owner_name="Priya",
        start=time(12, 0),
        end=time(22, 0),
        pet_name="Sunny",
        species="Bird",
        breed="Cockatiel",
        age=7,
        food_type="Seed and Pellet Mix",
        medication="antibiotic drops",
        care_task_type="veterinarian appointment",
        requested_flexibility=Flexibility.FLEXIBLE,
        expected_flexibility=Flexibility.FIXED,
    ),
)


class SequenceAIClient:
    """Small deterministic client used by Phase 6.9 service tests."""

    def __init__(self, *responses: object):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def generate_json(self, system_prompt: str, user_payload: dict) -> object:
        self.calls.append((system_prompt, user_payload))
        if not self.responses:
            raise AssertionError("The workflow made an unexpected extra AI call.")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _minutes_after(value: time, minutes: int) -> time:
    return (datetime.combine(date.today(), value) + timedelta(minutes=minutes)).time()


def _build_owner(profile: ProfileCase) -> tuple[Owner, Pet, Task]:
    owner = Owner(
        name=profile.owner_name,
        startTime=profile.start,
        endTime=profile.end,
        preferences={},
    )
    pet = Pet(
        name=profile.pet_name,
        species=profile.species,
        breed=profile.breed,
        age=profile.age,
        foodType=profile.food_type,
        medication=profile.medication,
        energyLevel=5,
    )
    owner.addPet(pet)

    task = Task(
        taskId=f"{profile.pet_name.lower()}-care",
        taskName=f"{profile.pet_name} care",
        taskType=profile.care_task_type,
        durationMinutes=20,
        priority=Priority.MEDIUM,
        pet=pet,
        preferredTime=_minutes_after(profile.start, 60),
        flexibility=profile.requested_flexibility,
        recurrence="none",
        dueDate=date.today(),
    )
    owner.scheduler.addTask(task)
    return owner, pet, task


def _build_conflict_owner(profile: ProfileCase) -> tuple[Owner, Pet, Task, Task, str]:
    owner = Owner(
        name=profile.owner_name,
        startTime=profile.start,
        endTime=profile.end,
        preferences={},
    )
    pet = Pet(
        name=profile.pet_name,
        species=profile.species,
        breed=profile.breed,
        age=profile.age,
        foodType=profile.food_type,
        medication=profile.medication,
        energyLevel=6,
    )
    owner.addPet(pet)

    conflict_time = _minutes_after(profile.start, 60)
    proposed_time = _minutes_after(profile.start, 180).strftime("%H:%M")

    protected = Task(
        taskId=f"{profile.pet_name.lower()}-med",
        taskName="Give medication",
        taskType="medication",
        durationMinutes=15,
        priority=Priority.HIGH,
        pet=pet,
        preferredTime=conflict_time,
        flexibility=Flexibility.FLEXIBLE,  # Must be forced to fixed.
        recurrence="none",
        dueDate=date.today(),
    )
    movable = Task(
        taskId=f"{profile.pet_name.lower()}-move",
        taskName="Flexible care activity",
        taskType="walk" if profile.species in {"Dog", "Rabbit"} else "play",
        durationMinutes=30,
        priority=Priority.MEDIUM,
        pet=pet,
        preferredTime=conflict_time,
        flexibility=Flexibility.FLEXIBLE,
        recurrence="none",
        dueDate=date.today(),
    )
    owner.scheduler.addTask(protected)
    owner.scheduler.addTask(movable)
    return owner, pet, protected, movable, proposed_time


def _critic_response(protected: Task, movable: Task) -> dict:
    return {
        "status": "needs_revision",
        "summary": "A fixed medication task overlaps a flexible care task.",
        "issues": [
            {
                "issue_type": "schedule_conflict",
                "task_ids": [protected.taskId, movable.taskId],
                "severity": "high",
                "explanation": "The two reviewed task windows overlap.",
                "rule_sections": [],
            }
        ],
        "confidence": 0.94,
    }


def _valid_repair_response(movable: Task, new_time: str) -> dict:
    return {
        "proposed_changes": [
            {
                "task_id": movable.taskId,
                "action": "move",
                "original_time": movable.preferredTime.strftime("%H:%M"),
                "new_time": new_time,
                "reason": "Move the flexible task inside availability and keep medication fixed.",
            }
        ],
        "summary": "Move only the flexible task.",
    }


def _invalid_medication_move(protected: Task, new_time: str) -> dict:
    return {
        "proposed_changes": [
            {
                "task_id": protected.taskId,
                "action": "move",
                "original_time": protected.preferredTime.strftime("%H:%M"),
                "new_time": new_time,
                "reason": "Unsafe attempt to move a medication task.",
            }
        ],
        "summary": "Move the medication task.",
    }


def _sentinel(client: SequenceAIClient) -> PawPalSentinel:
    return PawPalSentinel(
        client,
        rule_retriever=lambda _query, _path, _top_k: [],
        enable_logging=False,
    )


@pytest.mark.parametrize("profile", PROFILE_CASES, ids=lambda p: p.owner_name)
def test_diverse_owner_pet_and_task_profiles_round_trip(
    profile: ProfileCase,
    tmp_path: Path,
) -> None:
    """Exercise owner, species, breed, age, food, medication, and flexibility."""
    owner, pet, task = _build_owner(profile)
    path = tmp_path / f"{profile.owner_name.lower()}-data.json"

    owner.save_to_json(path)
    loaded = Owner.load_from_json(path)

    assert loaded is not None
    assert loaded.name == profile.owner_name
    assert loaded.startTime == profile.start
    assert loaded.endTime == profile.end
    assert loaded.availableMinutes == owner.availableMinutes

    assert len(loaded.pets) == 1
    loaded_pet = loaded.pets[0]
    assert loaded_pet.name == profile.pet_name
    assert loaded_pet.species == profile.species
    assert loaded_pet.breed == profile.breed
    assert loaded_pet.age == profile.age
    assert loaded_pet.foodType == profile.food_type
    assert loaded_pet.medication == profile.medication

    assert len(loaded.scheduler.tasks) == 1
    loaded_task = loaded.scheduler.tasks[0]
    assert loaded_task.flexibility is profile.expected_flexibility
    assert task.flexibility is profile.expected_flexibility

    snapshot = build_schedule_snapshot(loaded)
    task_payload = task_snapshot_to_dict(snapshot.tasks[0])
    assert set(task_payload) == {
        "task_id",
        "task_name",
        "task_type",
        "duration_minutes",
        "priority",
        "pet_id",
        "pet_name",
        "preferred_time",
        "recurrence",
        "due_date",
        "flexibility",
    }
    assert "foodType" not in task_payload
    assert "medication" not in task_payload


@pytest.mark.parametrize("profile", PROFILE_CASES[:3], ids=lambda p: p.owner_name)
def test_ai_review_does_not_mutate_diverse_profiles_before_approval(
    profile: ProfileCase,
) -> None:
    owner, pet, protected, movable, proposed_time = _build_conflict_owner(profile)
    original_times = {task.taskId: task.preferredTime for task in owner.scheduler.tasks}
    original_pet = (
        pet.species,
        pet.breed,
        pet.age,
        pet.foodType,
        pet.medication,
    )

    client = SequenceAIClient(
        _critic_response(protected, movable),
        _valid_repair_response(movable, proposed_time),
    )
    run = _sentinel(client).review_plan(owner)

    assert run.status is WorkflowStatus.AWAITING_OWNER_APPROVAL
    assert run.can_approve is True
    assert protected.flexibility is Flexibility.FIXED
    assert {task.taskId: task.preferredTime for task in owner.scheduler.tasks} == original_times
    assert (
        pet.species,
        pet.breed,
        pet.age,
        pet.foodType,
        pet.medication,
    ) == original_pet
    assert len(client.calls) == 2


@pytest.mark.parametrize("profile", PROFILE_CASES[:3], ids=lambda p: p.owner_name)
def test_reject_preserves_original_times_for_diverse_profiles(profile: ProfileCase) -> None:
    owner, _pet, protected, movable, proposed_time = _build_conflict_owner(profile)
    original_times = {task.taskId: task.preferredTime for task in owner.scheduler.tasks}
    sentinel = _sentinel(
        SequenceAIClient(
            _critic_response(protected, movable),
            _valid_repair_response(movable, proposed_time),
        )
    )

    run = sentinel.review_plan(owner)
    result = sentinel.reject(run)

    assert result.rejected is True
    assert {task.taskId: task.preferredTime for task in owner.scheduler.tasks} == original_times


def test_approved_change_persists_after_reload_and_preserves_profile(tmp_path: Path) -> None:
    profile = PROFILE_CASES[1]  # Cat, Siamese, insulin, wet food, age 12.
    owner, pet, protected, movable, proposed_time = _build_conflict_owner(profile)
    original_protected_time = protected.preferredTime
    original_pet_fields = (
        pet.species,
        pet.breed,
        pet.age,
        pet.foodType,
        pet.medication,
    )
    data_file = tmp_path / "approved.json"

    sentinel = _sentinel(
        SequenceAIClient(
            _critic_response(protected, movable),
            _valid_repair_response(movable, proposed_time),
        )
    )
    run = sentinel.review_plan(owner)
    result = sentinel.approve(owner, run, data_file=data_file)

    assert result.success is True
    assert result.status is ApprovalStatus.APPROVED_AND_APPLIED
    assert protected.preferredTime == original_protected_time
    assert movable.preferredTime.strftime("%H:%M") == proposed_time

    reloaded = Owner.load_from_json(data_file)
    assert reloaded is not None
    tasks = {task.taskId: task for task in reloaded.scheduler.tasks}
    assert tasks[protected.taskId].preferredTime == original_protected_time
    assert tasks[movable.taskId].preferredTime.strftime("%H:%M") == proposed_time
    reloaded_pet = reloaded.pets[0]
    assert (
        reloaded_pet.species,
        reloaded_pet.breed,
        reloaded_pet.age,
        reloaded_pet.foodType,
        reloaded_pet.medication,
    ) == original_pet_fields


def test_invalid_proposal_cannot_be_approved_and_never_gets_third_attempt(tmp_path: Path) -> None:
    profile = PROFILE_CASES[0]
    owner, _pet, protected, movable, proposed_time = _build_conflict_owner(profile)
    original_times = {task.taskId: task.preferredTime for task in owner.scheduler.tasks}
    client = SequenceAIClient(
        _critic_response(protected, movable),
        _invalid_medication_move(protected, proposed_time),
        _invalid_medication_move(protected, _minutes_after(profile.start, 240).strftime("%H:%M")),
    )
    sentinel = _sentinel(client)

    run = sentinel.review_plan(owner)
    result = sentinel.approve(owner, run, data_file=tmp_path / "should-not-save.json")

    assert run.status is WorkflowStatus.HUMAN_REVIEW_REQUIRED
    assert len(run.repair_attempts) == 2
    assert len(client.calls) == 3  # One critic call plus exactly two repair calls.
    assert result.success is False
    assert result.status is ApprovalStatus.NOT_APPROVABLE
    assert {task.taskId: task.preferredTime for task in owner.scheduler.tasks} == original_times
    assert not (tmp_path / "should-not-save.json").exists()


def test_schedule_edit_after_review_is_rejected_as_stale(tmp_path: Path) -> None:
    profile = PROFILE_CASES[2]
    owner, _pet, protected, movable, proposed_time = _build_conflict_owner(profile)
    original_times = {task.taskId: task.preferredTime for task in owner.scheduler.tasks}
    sentinel = _sentinel(
        SequenceAIClient(
            _critic_response(protected, movable),
            _valid_repair_response(movable, proposed_time),
        )
    )
    run = sentinel.review_plan(owner)

    # Simulates an owner/task edit that the Streamlit UI must use to clear the
    # pending proposal. The backend still rejects it if a UI path misses that clear.
    owner.endTime = _minutes_after(owner.endTime, -30)
    owner.scheduler.timeAvailable = owner.availableMinutes

    result = sentinel.approve(owner, run, data_file=tmp_path / "stale.json")

    assert result.success is False
    assert result.status is ApprovalStatus.STALE_PROPOSAL
    assert {task.taskId: task.preferredTime for task in owner.scheduler.tasks} == original_times
    assert not (tmp_path / "stale.json").exists()


def test_refresh_or_reload_does_not_auto_apply_pending_proposal(tmp_path: Path) -> None:
    profile = PROFILE_CASES[0]
    owner, _pet, protected, movable, proposed_time = _build_conflict_owner(profile)
    original_times = {task.taskId: task.preferredTime for task in owner.scheduler.tasks}

    run = _sentinel(
        SequenceAIClient(
            _critic_response(protected, movable),
            _valid_repair_response(movable, proposed_time),
        )
    ).review_plan(owner)
    assert run.status is WorkflowStatus.AWAITING_OWNER_APPROVAL

    # Saving/reloading the live owner simulates a browser refresh or process
    # restart. Unapproved proposal data is not written into Task objects.
    data_file = tmp_path / "refresh.json"
    owner.save_to_json(data_file)
    reloaded = Owner.load_from_json(data_file)

    assert reloaded is not None
    assert {task.taskId: task.preferredTime for task in reloaded.scheduler.tasks} == original_times


def test_normal_scheduler_still_works_without_any_ai_client() -> None:
    owner, _pet, task = _build_owner(PROFILE_CASES[0])
    plan = owner.scheduler.generatePlan()

    assert plan == [task]
    assert owner.scheduler.planGenerated is True