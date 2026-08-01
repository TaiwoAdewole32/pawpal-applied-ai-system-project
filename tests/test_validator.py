"""Direct unit tests for ScheduleValidator.validate() (Phase 2.6).

These tests call ScheduleValidator().validate(snapshot, changes) directly with
raw proposal dicts (as an AI would return them) rather than going through
apply_approved_changes(), so a failure here points straight at the validator.

Two required cases from the Phase 2.6 list are intentionally NOT duplicated
here because they are about apply-time behavior, not validate() itself, and
already have dedicated coverage:
  - "Stale proposal rejected at apply time"
      -> tests/test_apply_approved_changes.py::TestApplyApprovedChangesStaleness
  - "Valid approved proposal updates only preferredTime"
      -> tests/test_apply_approved_changes.py::TestApplyApprovedChangesSuccess

Fixtures are deliberately varied test-class by test-class (different owner
names, pets, species, availability windows, durations, task types) rather
than reusing one single profile throughout the file.
"""

import unittest
from datetime import time as Time

from pawpal_system import Owner, Pet, Priority, Task
from sentinel_models import ScheduleSnapshot, TaskSnapshot, build_schedule_snapshot
from schedule_validator import ScheduleValidator


# --- Shared low-level helpers -------------------------------------------------

def make_owner(name, start, end):
    return Owner(name=name, startTime=start, endTime=end, preferences={})


def make_pet(name, species="dog", breed="", age=3, foodType="dry", medication="none", energyLevel=5):
    return Pet(
        name=name, species=species, breed=breed, age=age,
        foodType=foodType, medication=medication, energyLevel=energyLevel,
    )


def make_task(pet, name, task_type, preferred_time, duration=20, priority=Priority.MEDIUM, flexibility=None):
    kwargs = dict(
        taskName=name, taskType=task_type, durationMinutes=duration,
        priority=priority, pet=pet, preferredTime=preferred_time,
    )
    if flexibility is not None:
        kwargs["flexibility"] = flexibility
    return Task(**kwargs)


def change(task_id, action="move", original_time=None, new_time=None, reason="a reasonable explanation"):
    return {
        "task_id": task_id,
        "action": action,
        "original_time": original_time,
        "new_time": new_time,
        "reason": reason,
    }


def make_task_snapshot(
    task_id, task_name="Task", task_type="walk", duration_minutes=20,
    priority="medium", pet_id="pet-1", pet_name="Pet",
    preferred_time="08:00", recurrence="none", due_date="2026-07-27",
    flexibility="flexible",
):
    """Construct a TaskSnapshot directly, bypassing Task/resolve_flexibility.

    Used only for tests that need a snapshot shape a real Task could never
    produce (e.g. a task_type of "medication" paired with flexibility
    "flexible" — Task.__post_init__ always auto-corrects that combination,
    so simulating it here is the only way to test the validator's own
    independent FIXED_TASK_TYPES defense rather than relying on the
    already-corrected flexibility field).
    """
    return TaskSnapshot(
        task_id=task_id, task_name=task_name, task_type=task_type,
        duration_minutes=duration_minutes, priority=priority,
        pet_id=pet_id, pet_name=pet_name, preferred_time=preferred_time,
        recurrence=recurrence, due_date=due_date, flexibility=flexibility,
    )


def make_snapshot(tasks, availability_start="07:00", availability_end="19:00",
                   owner_name="Snapshot Owner", unscheduled=(), version="v1"):
    return ScheduleSnapshot(
        owner_name=owner_name,
        availability_start=availability_start,
        availability_end=availability_end,
        tasks=tuple(tasks),
        unscheduled_task_ids=tuple(unscheduled),
        version=version,
    )


validator = ScheduleValidator()


# --- Valid proposals accepted --------------------------------------------------

class TestValidMovesAccepted(unittest.TestCase):
    def setUp(self):
        self.owner = make_owner("Amara Chen", Time(6, 0), Time(22, 0))
        self.pet = make_pet("Nimbus", species="cat")
        self.owner.addPet(self.pet)

    def test_valid_flexible_move_accepted(self):
        task = make_task(self.pet, "Brush Nimbus", "grooming", Time(9, 0), duration=15, priority=Priority.LOW)
        self.owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(task.taskId, "move", "09:00", "10:30", "owner requested later time")])

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(len(result.normalized_changes), 1)
        self.assertEqual(result.normalized_changes[0].new_time, "10:30")

    def test_valid_preferred_move_accepted(self):
        task = make_task(self.pet, "Feed Nimbus", "feeding", Time(8, 0), duration=10)
        self.owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(task.taskId, "move", "08:00", "08:30", "slight adjustment")])

        self.assertTrue(result.valid, result.errors)
        self.assertTrue(result.checks["times_valid"])
        self.assertTrue(result.checks["inside_availability"])

    def test_move_at_availability_start_boundary_accepted(self):
        task = make_task(self.pet, "Early Play", "play", Time(9, 0), duration=30)
        self.owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(task.taskId, "move", "09:00", "06:00", "moved to window start")])

        self.assertTrue(result.valid, result.errors)

    def test_move_ending_exactly_at_availability_end_accepted(self):
        task = make_task(self.pet, "Late Walk", "walk", Time(9, 0), duration=30)
        self.owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(task.taskId, "move", "09:00", "21:30", "moved to end of window")])

        self.assertTrue(result.valid, result.errors)

    def test_empty_proposal_list_is_valid(self):
        task = make_task(self.pet, "Idle Task", "walk", Time(9, 0))
        self.owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [])

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.normalized_changes, [])

    def test_empty_proposal_valid_even_with_preexisting_conflicts(self):
        overlap_a = make_task(self.pet, "Overlap A", "walk", Time(9, 0), duration=30)
        overlap_b = make_task(self.pet, "Overlap B", "play", Time(9, 10), duration=30)
        self.owner.scheduler.addTask(overlap_a)
        self.owner.scheduler.addTask(overlap_b)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [])

        self.assertTrue(result.valid, result.errors)


# --- Fixed / medication / veterinarian protection ------------------------------

class TestFixedAndMedicationProtection(unittest.TestCase):
    def setUp(self):
        self.owner = make_owner("Diego Ruiz", Time(8, 0), Time(18, 0))
        self.pet = make_pet("Rex", species="dog", breed="Labrador")
        self.owner.addPet(self.pet)

    def test_fixed_task_move_rejected(self):
        task = make_task(self.pet, "Rex training", "training", Time(10, 0), duration=45, flexibility="fixed")
        self.owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(task.taskId, "move", "10:00", "11:00", "try to move a fixed task")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["fixed_tasks_unchanged"])

    def test_medication_move_rejected_even_when_mislabeled_flexible(self):
        # Bypasses Task/resolve_flexibility's own auto-correction on purpose:
        # this proves the validator's FIXED_TASK_TYPES check is independent
        # defense-in-depth, not just a read of an already-corrected field.
        med_snapshot = make_task_snapshot(
            task_id="task-med-1", task_name="Give Rex insulin", task_type="medication",
            duration_minutes=10, priority="high", pet_id=self.pet.petId, pet_name="Rex",
            preferred_time="09:00", flexibility="flexible",
        )
        snapshot = make_snapshot([med_snapshot], availability_start="08:00", availability_end="18:00")

        result = validator.validate(snapshot, [change("task-med-1", "move", "09:00", "09:30", "attempted medication move")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["medication_tasks_unchanged"])

    def test_veterinarian_appointment_move_rejected(self):
        task = make_task(self.pet, "Rex checkup", "veterinarian appointment", Time(13, 0), duration=30)
        self.owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(task.taskId, "move", "13:00", "14:00", "try to move the vet visit")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["fixed_tasks_unchanged"])

    def test_keep_action_on_fixed_medication_task_is_not_rejected(self):
        task = make_task(self.pet, "Rex insulin", "medication", Time(9, 0), duration=10)
        self.owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(task.taskId, "keep", None, None, "no change needed")])

        self.assertTrue(result.valid, result.errors)
        self.assertTrue(result.checks["fixed_tasks_unchanged"])
        self.assertTrue(result.checks["medication_tasks_unchanged"])


# --- Task-identity checks -------------------------------------------------------

class TestTaskIdentityChecks(unittest.TestCase):
    def setUp(self):
        self.owner = make_owner("Priya Nair", Time(9, 0), Time(11, 0))
        self.pet = make_pet("Sushi", species="dog", breed="Corgi")
        self.owner.addPet(self.pet)
        self.task = make_task(self.pet, "Sushi walk", "walk", Time(9, 30), duration=20)
        self.owner.scheduler.addTask(self.task)
        self.snapshot = build_schedule_snapshot(self.owner)

    def test_unknown_task_id_rejected(self):
        result = validator.validate(self.snapshot, [change("does-not-exist", "move", "09:30", "10:00", "bogus id")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["task_ids_known"])

    def test_empty_string_task_id_rejected(self):
        result = validator.validate(self.snapshot, [change("", "move", "09:30", "10:00", "empty id")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_non_string_task_id_int_rejected(self):
        result = validator.validate(self.snapshot, [change(42, "move", "09:30", "10:00", "int id")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_none_task_id_rejected(self):
        result = validator.validate(self.snapshot, [change(None, "move", "09:30", "10:00", "null id")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_task_from_different_owner_snapshot_rejected(self):
        other_owner = make_owner("Owen Blake", Time(9, 0), Time(11, 0))
        other_pet = make_pet("Ash", species="cat")
        other_owner.addPet(other_pet)
        other_task = make_task(other_pet, "Ash groom", "grooming", Time(9, 30), duration=20)
        other_owner.scheduler.addTask(other_task)
        other_snapshot = build_schedule_snapshot(other_owner)

        # other_task.taskId is real, but only within other_snapshot — not self.snapshot.
        result = validator.validate(self.snapshot, [change(other_task.taskId, "move", "09:30", "10:00", "cross-owner id")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["task_ids_known"])
        self.assertNotEqual(other_snapshot.version, self.snapshot.version)


# --- Action allowlist -----------------------------------------------------------

class TestActionAllowlist(unittest.TestCase):
    def setUp(self):
        self.owner = make_owner("Sofia Marin", Time(5, 30), Time(23, 0))
        self.pet = make_pet("Loki", species="dog", breed="Husky")
        self.owner.addPet(self.pet)
        self.task = make_task(self.pet, "Loki play", "play", Time(10, 0), duration=25)
        self.owner.scheduler.addTask(self.task)
        self.snapshot = build_schedule_snapshot(self.owner)

    def test_unknown_action_rejected(self):
        result = validator.validate(self.snapshot, [change(self.task.taskId, "reschedule_forever", "10:00", "11:00", "bad action")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["actions_allowed"])

    def test_delete_action_rejected(self):
        result = validator.validate(self.snapshot, [change(self.task.taskId, "delete", "10:00", None, "remove it")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["actions_allowed"])

    def test_create_action_rejected(self):
        result = validator.validate(self.snapshot, [change(self.task.taskId, "create", None, "12:00", "add a new task")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["actions_allowed"])


# --- Schema and field checks ----------------------------------------------------

class TestSchemaAndFieldChecks(unittest.TestCase):
    def setUp(self):
        self.owner = make_owner("Malik Osei", Time(7, 15), Time(20, 45))
        self.pet_a = make_pet("Coco", species="cat")
        self.pet_b = make_pet("Pepper", species="dog", breed="Beagle")
        self.owner.addPet(self.pet_a)
        self.owner.addPet(self.pet_b)
        self.task_a = make_task(self.pet_a, "Coco groom", "grooming", Time(10, 0), duration=45)
        self.task_b = make_task(self.pet_b, "Pepper walk", "walk", Time(15, 0), duration=10)
        self.owner.scheduler.addTask(self.task_a)
        self.owner.scheduler.addTask(self.task_b)
        self.snapshot = build_schedule_snapshot(self.owner)

    def test_extra_field_rejected(self):
        bad = change(self.task_a.taskId, "move", "10:00", "11:00", "test")
        bad["new_duration"] = 60
        result = validator.validate(self.snapshot, [bad])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_duration_change_field_rejected(self):
        bad = change(self.task_a.taskId, "move", "10:00", "11:00", "test")
        bad["duration_minutes"] = 90
        result = validator.validate(self.snapshot, [bad])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_pet_change_field_rejected(self):
        bad = change(self.task_a.taskId, "move", "10:00", "11:00", "test")
        bad["new_pet"] = self.pet_b.petId
        result = validator.validate(self.snapshot, [bad])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_recurrence_change_field_rejected(self):
        bad = change(self.task_a.taskId, "move", "10:00", "11:00", "test")
        bad["recurrence"] = "daily"
        result = validator.validate(self.snapshot, [bad])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_missing_reason_key_rejected(self):
        bad = change(self.task_a.taskId, "move", "10:00", "11:00", "test")
        del bad["reason"]
        result = validator.validate(self.snapshot, [bad])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_missing_action_key_rejected(self):
        bad = change(self.task_a.taskId, "move", "10:00", "11:00", "test")
        del bad["action"]
        result = validator.validate(self.snapshot, [bad])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_duplicate_task_changes_rejected(self):
        result = validator.validate(self.snapshot, [
            change(self.task_a.taskId, "move", "10:00", "11:00", "first proposal"),
            change(self.task_a.taskId, "move", "10:00", "12:00", "second, conflicting proposal"),
        ])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_too_many_changes_for_known_tasks_rejected(self):
        result = validator.validate(self.snapshot, [
            change("phantom-task-1", "move", "09:00", "10:00", "does not exist"),
            change("phantom-task-2", "move", "09:00", "10:00", "does not exist either"),
            change("phantom-task-3", "move", "09:00", "10:00", "still does not exist"),
        ])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])
        self.assertTrue(any("Too many proposed changes" in e for e in result.errors))

    def test_reason_empty_string_allowed(self):
        result = validator.validate(self.snapshot, [change(self.task_a.taskId, "move", "10:00", "11:00", "")])

        self.assertTrue(result.valid, result.errors)

    def test_reason_extremely_long_rejected(self):
        huge_reason = "x" * 5000
        result = validator.validate(self.snapshot, [change(self.task_a.taskId, "move", "10:00", "11:00", huge_reason)])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])


# --- Type validation on the top-level payload and individual fields -------------

class TestTypeValidation(unittest.TestCase):
    def setUp(self):
        self.owner = make_owner("Elena Petrova", Time(6, 0), Time(20, 0))
        self.pet = make_pet("Basil", species="dog", breed="Poodle")
        self.owner.addPet(self.pet)
        self.task = make_task(self.pet, "Basil walk", "walk", Time(11, 0), duration=20)
        self.owner.scheduler.addTask(self.task)
        self.snapshot = build_schedule_snapshot(self.owner)

    def test_none_proposal_handled(self):
        result = validator.validate(self.snapshot, None)

        self.assertFalse(result.valid)
        self.assertEqual(result.normalized_changes, [])

    def test_dict_instead_of_list_handled(self):
        result = validator.validate(self.snapshot, {"task_id": self.task.taskId})

        self.assertFalse(result.valid)

    def test_boolean_instead_of_list_handled(self):
        result = validator.validate(self.snapshot, True)

        self.assertFalse(result.valid)

    def test_new_time_non_string_int_rejected(self):
        bad = change(self.task.taskId, "move", "11:00", None, "bad type")
        bad["new_time"] = 1100
        result = validator.validate(self.snapshot, [bad])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_new_time_list_type_rejected(self):
        bad = change(self.task.taskId, "move", "11:00", None, "bad type")
        bad["new_time"] = ["11:30"]
        result = validator.validate(self.snapshot, [bad])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_original_time_dict_type_rejected(self):
        bad = change(self.task.taskId, "move", None, "11:30", "bad type")
        bad["original_time"] = {"hour": 11}
        result = validator.validate(self.snapshot, [bad])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])

    def test_new_time_float_type_rejected(self):
        bad = change(self.task.taskId, "move", "11:00", None, "bad type")
        bad["new_time"] = 11.5
        result = validator.validate(self.snapshot, [bad])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["schema_valid"])


# --- Strict HH:MM time parsing ---------------------------------------------------

class TestTimeParsing(unittest.TestCase):
    def setUp(self):
        self.owner = make_owner("Grace Kim", Time(7, 0), Time(19, 0))
        self.pet = make_pet("Momo", species="cat")
        self.owner.addPet(self.pet)
        self.task = make_task(self.pet, "Feed Momo", "feeding", Time(12, 0), duration=20)
        self.owner.scheduler.addTask(self.task)
        self.snapshot = build_schedule_snapshot(self.owner)

    def test_malformed_time_missing_leading_zero_rejected(self):
        result = validator.validate(self.snapshot, [change(self.task.taskId, "move", "12:00", "7:00", "test")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["times_valid"])

    def test_malformed_time_out_of_range_minutes_rejected(self):
        result = validator.validate(self.snapshot, [change(self.task.taskId, "move", "12:00", "19:75", "test")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["times_valid"])

    def test_malformed_time_hour_24_rejected(self):
        result = validator.validate(self.snapshot, [change(self.task.taskId, "move", "12:00", "24:00", "test")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["times_valid"])

    def test_time_outside_availability_rejected(self):
        result = validator.validate(self.snapshot, [change(self.task.taskId, "move", "12:00", "20:00", "too late")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["inside_availability"])

    def test_task_ending_outside_availability_rejected(self):
        task = make_task(self.pet, "Long grooming", "grooming", Time(9, 0), duration=60)
        self.owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(task.taskId, "move", "09:00", "18:30", "starts fine, ends past close")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["inside_availability"])


# --- Original-time consistency ---------------------------------------------------

class TestOriginalTimeConsistency(unittest.TestCase):
    def test_original_time_mismatch_rejected(self):
        owner = make_owner("Tariq Bello", Time(8, 0), Time(16, 0))
        pet = make_pet("Biscuit", species="dog", breed="Pug")
        owner.addPet(pet)
        task = make_task(pet, "Biscuit walk", "walk", Time(9, 0), duration=20)
        owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(owner)

        result = validator.validate(snapshot, [change(task.taskId, "move", "09:15", "10:00", "wrong original time")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["times_valid"])


# --- Conflict detection on the candidate plan ------------------------------------

class TestConflictDetection(unittest.TestCase):
    def setUp(self):
        self.owner = make_owner("Hana Suzuki", Time(6, 0), Time(22, 0))
        self.pet = make_pet("Ziggy", species="dog", breed="Border Collie")
        self.owner.addPet(self.pet)

    def test_new_conflict_rejected(self):
        task_a = make_task(self.pet, "Ziggy morning walk", "walk", Time(8, 0), duration=20)
        task_b = make_task(self.pet, "Ziggy fetch", "play", Time(10, 0), duration=20)
        self.owner.scheduler.addTask(task_a)
        self.owner.scheduler.addTask(task_b)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(task_b.taskId, "move", "10:00", "08:10", "move fetch into the walk's slot")])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["no_new_conflicts"])

    def test_preexisting_conflict_between_untouched_tasks_does_not_fail_unrelated_move(self):
        overlap_a = make_task(self.pet, "Ziggy walk", "walk", Time(8, 0), duration=30)
        overlap_b = make_task(self.pet, "Ziggy play", "play", Time(8, 10), duration=30)
        unrelated = make_task(self.pet, "Ziggy groom", "grooming", Time(14, 0), duration=20)
        self.owner.scheduler.addTask(overlap_a)
        self.owner.scheduler.addTask(overlap_b)
        self.owner.scheduler.addTask(unrelated)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(unrelated.taskId, "move", "14:00", "15:00", "unrelated move")])

        self.assertTrue(result.valid, result.errors)
        self.assertTrue(result.checks["no_new_conflicts"])


# --- Availability-window shape ----------------------------------------------------

class TestAvailabilityWindowShape(unittest.TestCase):
    def test_zero_width_availability_window_rejected(self):
        snapshot = make_snapshot(
            [make_task_snapshot("task-1", preferred_time="10:00")],
            availability_start="10:00", availability_end="10:00",
        )

        result = validator.validate(snapshot, [])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["inside_availability"])
        self.assertTrue(any("overnight" in e for e in result.errors))

    def test_overnight_window_rejected_with_specific_message(self):
        snapshot = make_snapshot(
            [make_task_snapshot("task-1", preferred_time="22:00")],
            availability_start="22:00", availability_end="06:00",
        )

        result = validator.validate(snapshot, [change("task-1", "move", "22:00", "23:00", "test")])

        self.assertFalse(result.valid)
        self.assertTrue(any(
            "Sentinel doesn't yet support overnight availability windows" in e
            for e in result.errors
        ))


# --- Mutation safety ---------------------------------------------------------------

class TestNoMutation(unittest.TestCase):
    def setUp(self):
        self.owner = make_owner("Noah Fitzgerald", Time(7, 0), Time(19, 0))
        self.pet = make_pet("Daisy", species="dog", breed="Golden Retriever")
        self.owner.addPet(self.pet)
        self.task = make_task(self.pet, "Daisy walk", "walk", Time(9, 0), duration=20)
        self.owner.scheduler.addTask(self.task)

    def test_valid_proposal_does_not_mutate_snapshot(self):
        snapshot = build_schedule_snapshot(self.owner)
        original_task_snapshot = snapshot.tasks[0]

        validator.validate(snapshot, [change(self.task.taskId, "move", "09:00", "10:00", "test")])

        self.assertEqual(snapshot.tasks[0].preferred_time, "09:00")
        self.assertIs(snapshot.tasks[0], original_task_snapshot)
        self.assertEqual(self.task.preferredTime, Time(9, 0))

    def test_failed_proposal_does_not_mutate_snapshot(self):
        snapshot = build_schedule_snapshot(self.owner)
        original_task_snapshot = snapshot.tasks[0]

        validator.validate(snapshot, [change(self.task.taskId, "move", "09:00", "bad-time", "test")])

        self.assertEqual(snapshot.tasks[0].preferred_time, "09:00")
        self.assertIs(snapshot.tasks[0], original_task_snapshot)
        self.assertEqual(self.task.preferredTime, Time(9, 0))


# --- Multi-item proposals -----------------------------------------------------------

class TestMultiItemProposals(unittest.TestCase):
    def test_first_invalid_item_does_not_hide_second_valid_item(self):
        owner = make_owner("Yuki Tanaka", Time(6, 0), Time(21, 0))
        pet = make_pet("Kiwi", species="bird")
        owner.addPet(pet)
        bad_task = make_task(pet, "Kiwi flight time", "play", Time(9, 0), duration=20)
        good_task = make_task(pet, "Kiwi feeding", "feeding", Time(13, 0), duration=15)
        owner.scheduler.addTask(bad_task)
        owner.scheduler.addTask(good_task)
        snapshot = build_schedule_snapshot(owner)

        result = validator.validate(snapshot, [
            change(bad_task.taskId, "move", "09:00", "25:00", "malformed time"),
            change(good_task.taskId, "move", "13:00", "14:00", "perfectly fine move"),
        ])

        self.assertFalse(result.valid)
        self.assertFalse(result.checks["times_valid"])
        self.assertTrue(any(bad_task.taskId in e for e in result.errors))
        # Overall-invalid batches apply nothing, including the otherwise-good item.
        self.assertEqual(result.normalized_changes, [])


# --- keep / defer_for_review actions -------------------------------------------------

class TestKeepAndDeferActions(unittest.TestCase):
    def setUp(self):
        self.owner = make_owner("Fatima Zahra", Time(7, 0), Time(19, 0))
        self.pet = make_pet("Simba", species="cat")
        self.owner.addPet(self.pet)

    def test_keep_action_is_noop_valid(self):
        task = make_task(self.pet, "Simba feeding", "feeding", Time(8, 0), duration=10)
        self.owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(task.taskId, "keep", None, None, "schedule looks fine as-is")])

        self.assertTrue(result.valid, result.errors)

    def test_defer_for_review_action_is_valid(self):
        task_a = make_task(self.pet, "Simba vet visit", "veterinarian appointment", Time(9, 0), duration=30)
        task_b = make_task(self.pet, "Simba grooming", "grooming", Time(9, 15), duration=30, flexibility="fixed")
        self.owner.scheduler.addTask(task_a)
        self.owner.scheduler.addTask(task_b)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [
            change(task_a.taskId, "defer_for_review", None, None, "two fixed tasks overlap, needs a human"),
            change(task_b.taskId, "defer_for_review", None, None, "two fixed tasks overlap, needs a human"),
        ])

        self.assertTrue(result.valid, result.errors)

    def test_keep_action_skips_availability_check_for_out_of_window_preferred_time(self):
        # A task whose stored preferred_time already falls outside the owner's
        # (possibly since-narrowed) availability window. "keep" must not run
        # the per-item availability check at all — only "move" does.
        task = make_task(self.pet, "Simba late nap check", "grooming", Time(23, 0), duration=10)
        self.owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(self.owner)

        result = validator.validate(snapshot, [change(task.taskId, "keep", None, None, "leave the out-of-window task alone")])

        self.assertTrue(result.valid, result.errors)
        self.assertTrue(result.checks["inside_availability"])


if __name__ == "__main__":
    unittest.main()
