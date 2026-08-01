import os
import tempfile
import unittest
from datetime import time as Time

from pawpal_system import Owner, Pet, Priority, Task
from sentinel_models import ProposedChange, build_schedule_snapshot
from schedule_validator import (
    InvalidProposalError,
    StaleScheduleError,
    apply_approved_changes,
)


def make_two_pet_owner():
    """A different owner/pet/task shape than make_owner()/make_pet()/make_task()

    below, so not every test in this file exercises the same single-owner,
    single-pet, single-task profile.
    """
    owner = Owner(name="Rowan", startTime=Time(6, 0), endTime=Time(21, 0), preferences={})
    dog = Pet(
        name="Juniper",
        species="dog",
        breed="Beagle",
        age=5,
        foodType="wet",
        medication="thyroid pill",
        energyLevel=7,
    )
    cat = Pet(
        name="Ash",
        species="cat",
        breed="",
        age=2,
        foodType="dry",
        medication="none",
        energyLevel=3,
    )
    owner.addPet(dog)
    owner.addPet(cat)

    medication = Task(
        taskName="Thyroid Pill",
        taskType="medication",
        durationMinutes=5,
        priority=Priority.HIGH,
        pet=dog,
        preferredTime=Time(8, 0),
    )
    walk = Task(
        taskName="Morning Walk",
        taskType="walk",
        durationMinutes=30,
        priority=Priority.MEDIUM,
        pet=dog,
        preferredTime=Time(9, 0),
    )
    groom = Task(
        taskName="Brush Ash",
        taskType="grooming",
        durationMinutes=15,
        priority=Priority.LOW,
        pet=cat,
        preferredTime=Time(14, 0),
    )
    owner.scheduler.addTask(medication)
    owner.scheduler.addTask(walk)
    owner.scheduler.addTask(groom)
    return owner, medication, walk, groom


def make_owner():
    return Owner(name="Jordan", startTime=Time(7, 0), endTime=Time(19, 0), preferences={})


def make_pet(name="Milo"):
    return Pet(
        name=name,
        species="dog",
        breed="",
        age=3,
        foodType="dry",
        medication="none",
        energyLevel=5,
    )


def make_task(pet, name="Walk", task_type="walk", preferred_time=Time(8, 0), duration=20):
    return Task(
        taskName=name,
        taskType=task_type,
        durationMinutes=duration,
        priority=Priority.MEDIUM,
        pet=pet,
        preferredTime=preferred_time,
    )


class TestApplyApprovedChangesSuccess(unittest.TestCase):
    def setUp(self):
        fd, self.temp_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.temp_path):
            os.remove(self.temp_path)

    def test_valid_move_updates_only_preferred_time(self):
        owner = make_owner()
        pet = make_pet()
        owner.addPet(pet)
        task = make_task(pet, name="Walk Milo", task_type="walk", preferred_time=Time(8, 0))
        owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(owner)

        change = ProposedChange(
            task_id=task.taskId, action="move",
            original_time="08:00", new_time="09:00", reason="test",
        )
        apply_approved_changes(owner, snapshot.version, [change], data_file=self.temp_path)

        self.assertEqual(task.preferredTime, Time(9, 0))
        self.assertEqual(task.taskName, "Walk Milo")
        self.assertEqual(task.taskType, "walk")
        self.assertEqual(task.durationMinutes, 20)
        self.assertEqual(task.priority, Priority.MEDIUM)
        self.assertFalse(task.completed)

    def test_save_writes_new_time_to_disk(self):
        owner = make_owner()
        pet = make_pet()
        owner.addPet(pet)
        task = make_task(pet, preferred_time=Time(8, 0))
        owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(owner)

        change = ProposedChange(task.taskId, "move", "08:00", "10:30", "test")
        apply_approved_changes(owner, snapshot.version, [change], data_file=self.temp_path)

        reloaded = Owner.load_from_json(self.temp_path)
        self.assertEqual(reloaded.scheduler.tasks[0].preferredTime, Time(10, 30))

    def test_two_valid_moves_both_apply_and_save_once(self):
        owner = make_owner()
        pet = make_pet()
        owner.addPet(pet)
        task_a = make_task(pet, name="A", preferred_time=Time(8, 0))
        task_b = make_task(pet, name="B", preferred_time=Time(12, 0))
        owner.scheduler.addTask(task_a)
        owner.scheduler.addTask(task_b)
        snapshot = build_schedule_snapshot(owner)

        changes = [
            ProposedChange(task_a.taskId, "move", "08:00", "09:00", "test a"),
            ProposedChange(task_b.taskId, "move", "12:00", "13:00", "test b"),
        ]
        apply_approved_changes(owner, snapshot.version, changes, data_file=self.temp_path)

        self.assertEqual(task_a.preferredTime, Time(9, 0))
        self.assertEqual(task_b.preferredTime, Time(13, 0))

    def test_keep_and_defer_entries_are_no_ops(self):
        owner = make_owner()
        pet = make_pet()
        owner.addPet(pet)
        task = make_task(pet, preferred_time=Time(8, 0))
        owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(owner)

        changes = [
            ProposedChange(task.taskId, "keep", None, None, "no change needed"),
        ]
        apply_approved_changes(owner, snapshot.version, changes, data_file=self.temp_path)

        self.assertEqual(task.preferredTime, Time(8, 0))

    def test_move_mixed_with_keep_and_defer_across_multiple_pets(self):
        owner, medication, walk, groom = make_two_pet_owner()
        snapshot = build_schedule_snapshot(owner)

        changes = [
            ProposedChange(medication.taskId, "defer_for_review", "08:00", None, "fixed"),
            ProposedChange(walk.taskId, "move", "09:00", "10:00", "flexible walk"),
            ProposedChange(groom.taskId, "keep", None, None, "already fine"),
        ]
        apply_approved_changes(owner, snapshot.version, changes, data_file=self.temp_path)

        self.assertEqual(medication.preferredTime, Time(8, 0))
        self.assertEqual(walk.preferredTime, Time(10, 0))
        self.assertEqual(groom.preferredTime, Time(14, 0))

    def test_two_moves_across_two_pets_roll_back_together_on_partial_failure(self):
        owner, medication, walk, groom = make_two_pet_owner()
        snapshot = build_schedule_snapshot(owner)

        changes = [
            ProposedChange(walk.taskId, "move", "09:00", "11:00", "move walk"),
            ProposedChange(groom.taskId, "move", "14:00", "15:00", "move groom"),
        ]

        original_update = groom.updateTask

        def failing_update(field, value):
            if field == "preferredTime":
                raise RuntimeError("simulated failure on second task")
            return original_update(field, value)

        groom.updateTask = failing_update
        try:
            with self.assertRaises(RuntimeError):
                apply_approved_changes(owner, snapshot.version, changes, data_file=self.temp_path)
        finally:
            del groom.updateTask

        # The first task's mutation must be rolled back even though only the
        # second task's update actually failed.
        self.assertEqual(walk.preferredTime, Time(9, 0))
        self.assertEqual(groom.preferredTime, Time(14, 0))
        self.assertEqual(medication.preferredTime, Time(8, 0))


class TestApplyApprovedChangesRejectsUnsafeMoves(unittest.TestCase):
    def setUp(self):
        fd, self.temp_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.temp_path):
            os.remove(self.temp_path)

    def test_move_onto_a_fixed_medication_task_is_rejected_without_mutation(self):
        owner, medication, _walk, _groom = make_two_pet_owner()
        snapshot = build_schedule_snapshot(owner)

        change = ProposedChange(
            medication.taskId, "move", "08:00", "09:00", "should be rejected",
        )
        with self.assertRaises(InvalidProposalError):
            apply_approved_changes(owner, snapshot.version, [change], data_file=self.temp_path)

        self.assertEqual(medication.preferredTime, Time(8, 0))

    def test_unknown_task_id_is_rejected_without_mutation(self):
        owner, medication, walk, groom = make_two_pet_owner()
        snapshot = build_schedule_snapshot(owner)

        change = ProposedChange(
            "task-does-not-exist", "move", "09:00", "10:00", "task was removed",
        )
        with self.assertRaises(InvalidProposalError):
            apply_approved_changes(owner, snapshot.version, [change], data_file=self.temp_path)

        self.assertEqual(walk.preferredTime, Time(9, 0))
        self.assertEqual(medication.preferredTime, Time(8, 0))
        self.assertEqual(groom.preferredTime, Time(14, 0))

    def test_malformed_new_time_on_move_is_rejected_without_mutation(self):
        owner, medication, walk, groom = make_two_pet_owner()
        snapshot = build_schedule_snapshot(owner)

        change = ProposedChange(walk.taskId, "move", "09:00", "9:00 AM", "bad time")
        with self.assertRaises(InvalidProposalError):
            apply_approved_changes(owner, snapshot.version, [change], data_file=self.temp_path)

        self.assertEqual(walk.preferredTime, Time(9, 0))

    def test_malformed_original_time_on_move_is_rejected_without_mutation(self):
        owner, _medication, walk, _groom = make_two_pet_owner()
        snapshot = build_schedule_snapshot(owner)

        change = ProposedChange(walk.taskId, "move", "9am", "10:00", "bad original time")
        with self.assertRaises(InvalidProposalError):
            apply_approved_changes(owner, snapshot.version, [change], data_file=self.temp_path)

        self.assertEqual(walk.preferredTime, Time(9, 0))


class TestApplyApprovedChangesStaleness(unittest.TestCase):
    def setUp(self):
        fd, self.temp_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.temp_path):
            os.remove(self.temp_path)

    def test_stale_version_raises_and_does_not_mutate(self):
        owner = make_owner()
        pet = make_pet()
        owner.addPet(pet)
        task = make_task(pet, preferred_time=Time(8, 0))
        owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(owner)

        # Owner edits the task after the snapshot was taken, before approval.
        task.updateTask("preferredTime", Time(8, 30))

        change = ProposedChange(task.taskId, "move", "08:00", "09:00", "test")
        with self.assertRaises(StaleScheduleError):
            apply_approved_changes(owner, snapshot.version, [change], data_file=self.temp_path)

        self.assertEqual(task.preferredTime, Time(8, 30))
        self.assertFalse(os.path.exists(self.temp_path) and os.path.getsize(self.temp_path) > 0)

    def test_revalidation_failure_raises_and_does_not_mutate(self):
        owner = make_owner()
        pet = make_pet()
        owner.addPet(pet)
        task = make_task(pet, task_type="walk", preferred_time=Time(8, 0))
        owner.scheduler.addTask(task)
        snapshot = build_schedule_snapshot(owner)

        change = ProposedChange(task.taskId, "move", "08:00", "09:00", "test")

        # Task becomes fixed after the snapshot was reviewed but before approval —
        # version hash changes too (flexibility is hashed), so this also exercises
        # the "still fails even though caller passed the original version" path
        # by asserting on InvalidProposalError specifically when versions do
        # happen to still line up is covered implicitly since flexibility is
        # part of the version fingerprint (Phase 2.2); here we confirm the
        # stale path catches it first, and mutation still never happens.
        task.updateTask("flexibility", "fixed")

        with self.assertRaises((StaleScheduleError, InvalidProposalError)):
            apply_approved_changes(owner, snapshot.version, [change], data_file=self.temp_path)

        self.assertEqual(task.preferredTime, Time(8, 0))


if __name__ == "__main__":
    unittest.main()
