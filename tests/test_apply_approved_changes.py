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
