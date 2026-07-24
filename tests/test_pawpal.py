import os
import tempfile
import unittest
from datetime import time as Time, date, timedelta
from pawpal_system import Pet, Task, Scheduler, Priority, Owner


def make_owner(
    name="Jordan",
    start_time=Time(7, 0),
    end_time=Time(19, 0),
    preferences=None,
):
    """Create a consistent owner fixture for tests."""
    return Owner(
        name=name,
        startTime=start_time,
        endTime=end_time,
        preferences={} if preferences is None else dict(preferences),
    )


def make_owner_with_task(
    pet_name="Buddy",
    task_name="Walk",
    *,
    recurrence="none",
    completed=False,
):
    """Create an owner, one pet, and one registered task."""
    owner = make_owner()
    pet = make_pet(pet_name)
    owner.addPet(pet)

    task = make_task(pet, name=task_name)
    task.recurrence = recurrence

    if completed:
        task.markComplete()

    owner.scheduler.addTask(task)

    return owner, pet, task


def make_pet(name="Mochi"):
    return Pet(
        name=name,
        species="dog",
        breed="Shiba Inu",
        age=3,
        foodType="dry",
        medication="none",
        energyLevel=8,
    )


def make_task(pet, name="Morning Walk", preferred_time=Time(8, 0)):
    return Task(
        taskName=name,
        taskType="exercise",
        durationMinutes=30,
        priority=Priority.HIGH,
        pet=pet,
        preferredTime=preferred_time,
    )


class TestMarkComplete(unittest.TestCase):
    def test_mark_complete_sets_status_to_completed(self):
        pet = make_pet()
        task = make_task(pet)

        self.assertFalse(task.completed, "Task should start as not completed")

        task.markComplete()

        self.assertTrue(task.completed, "Task should be completed after calling markComplete()")


class TestAddTaskIncreasesCount(unittest.TestCase):
    def test_adding_task_increases_pet_task_count(self):
        pet = make_pet()
        scheduler = Scheduler(timeAvailable=120)

        count_before = len(pet.tasks)

        task = make_task(pet)
        scheduler.addTask(task)

        self.assertEqual(
            len(pet.tasks),
            count_before + 1,
            "Pet's task count should increase by 1 after addTask()",
        )


class TestFilterTasks(unittest.TestCase):
    def test_filter_by_completion_status(self):
        pet = make_pet()
        scheduler = Scheduler(timeAvailable=120)
        task_a = make_task(pet, name="Task A")
        task_b = make_task(pet, name="Task B")
        scheduler.addTask(task_a)
        scheduler.addTask(task_b)
        task_a.markComplete()

        self.assertEqual(len(scheduler.filterTasks(completed=True)),  1)
        self.assertEqual(len(scheduler.filterTasks(completed=False)), 1)

    def test_filter_by_pet_name_case_insensitive(self):
        pet_a = make_pet("Rex")
        pet_b = make_pet("Luna")
        scheduler = Scheduler(timeAvailable=120)
        scheduler.addTask(make_task(pet_a, name="Rex task"))
        scheduler.addTask(make_task(pet_b, name="Luna task"))

        self.assertEqual(len(scheduler.filterTasks(petName="Rex")),  1)
        self.assertEqual(len(scheduler.filterTasks(petName="rex")),  1)
        self.assertEqual(len(scheduler.filterTasks(petName="Luna")), 1)

    def test_combined_filter(self):
        pet = make_pet("Scout")
        scheduler = Scheduler(timeAvailable=120)
        t1 = make_task(pet, name="A")
        t2 = make_task(pet, name="B")
        scheduler.addTask(t1)
        scheduler.addTask(t2)
        t1.markComplete()

        pending_scout = scheduler.filterTasks(completed=False, petName="Scout")
        self.assertEqual(len(pending_scout), 1)
        self.assertEqual(pending_scout[0].taskName, "B")


class TestRecurrence(unittest.TestCase):
    def setUp(self):
        from datetime import date
        self.today = date.today()
        self.pet = make_pet()
        self.scheduler = Scheduler(timeAvailable=240)

    def _make_recurring(self, recurrence: str) -> Task:
        return Task(
            taskName="Recurring Task",
            taskType="exercise",
            durationMinutes=30,
            priority=Priority.HIGH,
            pet=self.pet,
            preferredTime=Time(8, 0),
            recurrence=recurrence,
            dueDate=self.today,
        )

    def test_daily_task_spawns_next_day(self):
        from datetime import timedelta
        task = self._make_recurring("daily")
        self.scheduler.addTask(task)

        next_task = self.scheduler.completeTask(task)

        self.assertTrue(task.completed)
        self.assertIsNotNone(next_task)
        self.assertEqual(next_task.dueDate, self.today + timedelta(days=1))
        self.assertEqual(next_task.recurrence, "daily")
        self.assertFalse(next_task.completed)
        self.assertIn(next_task, self.scheduler.tasks)

    def test_weekly_task_spawns_next_week(self):
        from datetime import timedelta
        task = self._make_recurring("weekly")
        self.scheduler.addTask(task)

        next_task = self.scheduler.completeTask(task)

        self.assertIsNotNone(next_task)
        self.assertEqual(next_task.dueDate, self.today + timedelta(weeks=1))
        self.assertEqual(next_task.recurrence, "weekly")

    def test_nonrecurring_task_spawns_nothing(self):
        task = self._make_recurring("none")
        self.scheduler.addTask(task)
        count_before = len(self.scheduler.tasks)

        next_task = self.scheduler.completeTask(task)

        self.assertIsNone(next_task)
        self.assertEqual(len(self.scheduler.tasks), count_before)

    def test_spawned_task_inherits_all_fields(self):
        task = self._make_recurring("daily")
        self.scheduler.addTask(task)

        next_task = self.scheduler.completeTask(task)

        self.assertEqual(next_task.taskName,       task.taskName)
        self.assertEqual(next_task.taskType,       task.taskType)
        self.assertEqual(next_task.durationMinutes, task.durationMinutes)
        self.assertEqual(next_task.priority,       task.priority)
        self.assertEqual(next_task.preferredTime,  task.preferredTime)
        self.assertEqual(next_task.pet,            task.pet)

    def test_unrecognized_recurrence_spawns_nothing(self):
        task = self._make_recurring("monthly")
        self.scheduler.addTask(task)
        count_before = len(self.scheduler.tasks)

        next_task = self.scheduler.completeTask(task)

        self.assertIsNone(next_task)
        self.assertEqual(len(self.scheduler.tasks), count_before)

    def test_completing_already_completed_task_does_not_double_spawn(self):
        task = self._make_recurring("daily")
        self.scheduler.addTask(task)

        first_spawn = self.scheduler.completeTask(task)
        second_call = self.scheduler.completeTask(task)

        self.assertIsNotNone(first_spawn)
        self.assertIsNone(second_call)
        self.assertEqual(
            len([t for t in self.scheduler.tasks if t.taskName == "Recurring Task"]),
            2,  # original + exactly one spawn, not two
        )


class TestGeneratePlan(unittest.TestCase):
    def setUp(self):
        from datetime import date
        self.today = date.today()
        self.pet = make_pet()

    def _make_task(self, name, preferred_time, duration=30, priority=Priority.HIGH):
        return Task(
            taskName=name,
            taskType="exercise",
            durationMinutes=duration,
            priority=priority,
            pet=self.pet,
            preferredTime=preferred_time,
            dueDate=self.today,
        )

    def test_no_start_time_schedules_tasks_that_fit(self):
        t1 = self._make_task("Walk", Time(8,  0), duration=30, priority=Priority.HIGH)
        t2 = self._make_task("Feed", Time(12, 0), duration=20, priority=Priority.MEDIUM)
        t3 = self._make_task("Bath", Time(18, 0), duration=60, priority=Priority.LOW)
        s = Scheduler(tasks=[t1, t2, t3], timeAvailable=60)
        result = s.generatePlan()
        self.assertIn(t1, result)
        self.assertIn(t2, result)
        self.assertNotIn(t3, result)
        self.assertIn(t3, s.unscheduledTasks)

    def test_no_start_time_plan_sorted_by_preferred_time(self):
        t_late  = self._make_task("Late",  Time(14, 0), duration=10)
        t_early = self._make_task("Early", Time(7,  0), duration=10)
        result = Scheduler(tasks=[t_late, t_early], timeAvailable=60).generatePlan()
        self.assertEqual(result[0].taskName, "Early")
        self.assertEqual(result[1].taskName, "Late")

    def test_completed_tasks_excluded(self):
        t_done = self._make_task("Done",    Time(8, 0), duration=20)
        t_done.markComplete()
        t_pend = self._make_task("Pending", Time(9, 0), duration=20)
        result = Scheduler(tasks=[t_done, t_pend], timeAvailable=120).generatePlan()
        self.assertNotIn(t_done, result)
        self.assertIn(t_pend, result)

    def test_with_start_time_rejects_task_outside_window(self):
        t_in  = self._make_task("Inside",  Time(8,  0), duration=30)
        t_out = self._make_task("Outside", Time(20, 0), duration=30)
        s = Scheduler(tasks=[t_in, t_out], timeAvailable=60, startTime=Time(8, 0))
        result = s.generatePlan()
        self.assertIn(t_in, result)
        self.assertNotIn(t_out, result)
        self.assertIn(t_out, s.unscheduledTasks)

    def test_with_start_time_rejects_task_that_overruns_window(self):
        # Window 08:00–09:00; task at 08:45 + 30 min ends at 09:15
        t = self._make_task("Overrun", Time(8, 45), duration=30)
        s = Scheduler(tasks=[t], timeAvailable=60, startTime=Time(8, 0))
        self.assertEqual(s.generatePlan(), [])
        self.assertIn(t, s.unscheduledTasks)

    def test_generateplan_resets_state_on_repeated_calls(self):
        t = self._make_task("Walk", Time(8, 0), duration=20)
        s = Scheduler(tasks=[t], timeAvailable=60)
        s.generatePlan()
        self.assertEqual(s.generatePlan().count(t), 1)


class TestSortingCorrectness(unittest.TestCase):
    def setUp(self):
        self.pet = make_pet()

    def _make_task(self, name, preferred_time, priority=Priority.HIGH):
        return Task(
            taskName=name,
            taskType="exercise",
            durationMinutes=15,
            priority=priority,
            pet=self.pet,
            preferredTime=preferred_time,
        )

    def test_sort_by_time_returns_chronological_order(self):
        t_noon = self._make_task("Noon", Time(12, 0))
        t_dawn = self._make_task("Dawn", Time(6, 0))
        t_dusk = self._make_task("Dusk", Time(19, 0))
        scheduler = Scheduler(tasks=[t_noon, t_dawn, t_dusk], timeAvailable=120)

        ordered = scheduler.sort_by_time()

        self.assertEqual([t.taskName for t in ordered], ["Dawn", "Noon", "Dusk"])

    def test_sort_by_time_is_stable_for_equal_times(self):
        t_first = self._make_task("First", Time(8, 0))
        t_second = self._make_task("Second", Time(8, 0))
        scheduler = Scheduler(tasks=[t_first, t_second], timeAvailable=60)

        ordered = scheduler.sort_by_time()

        self.assertEqual([t.taskName for t in ordered], ["First", "Second"])

    def test_sort_by_time_on_empty_list_returns_empty_list(self):
        scheduler = Scheduler(timeAvailable=60)
        self.assertEqual(scheduler.sort_by_time(), [])

    def test_sort_tasks_by_priority_highest_first(self):
        t_low = self._make_task("Low", Time(8, 0), priority=Priority.LOW)
        t_high = self._make_task("High", Time(9, 0), priority=Priority.HIGH)
        t_med = self._make_task("Medium", Time(10, 0), priority=Priority.MEDIUM)
        scheduler = Scheduler(tasks=[t_low, t_high, t_med], timeAvailable=90)

        ordered = scheduler.sortTasksByPriority()

        self.assertEqual([t.taskName for t in ordered], ["High", "Medium", "Low"])

    def test_sort_tasks_by_priority_breaks_ties_by_time(self):
        t_late = self._make_task("Late High", Time(17, 0), priority=Priority.HIGH)
        t_early = self._make_task("Early High", Time(7, 0), priority=Priority.HIGH)
        scheduler = Scheduler(tasks=[t_late, t_early], timeAvailable=60)

        ordered = scheduler.sortTasksByPriority()

        self.assertEqual([t.taskName for t in ordered], ["Early High", "Late High"])


    def test_sort_tasks_by_priority_first_then_time(self):
        t_low_early = self._make_task("Low Early", Time(7, 0), priority=Priority.LOW)
        t_high_late = self._make_task("High Late", Time(17, 0), priority=Priority.HIGH)
        t_medium_morning = self._make_task("Medium Morning", Time(9, 0), priority=Priority.MEDIUM)
        t_high_early = self._make_task("High Early", Time(8, 0), priority=Priority.HIGH)

        scheduler = Scheduler(
            tasks=[t_low_early, t_high_late, t_medium_morning, t_high_early],
            timeAvailable=120,
        )

        ordered = scheduler.sortTasksByPriority()

        self.assertEqual(
            [t.taskName for t in ordered],
            ["High Early", "High Late", "Medium Morning", "Low Early"],
        )
class TestConflictDetection(unittest.TestCase):
    def setUp(self):
        self.pet = make_pet("Buddy")

    def _make_task(self, name, preferred_time, duration=30, pet=None, priority=Priority.HIGH):
        return Task(
            taskName=name,
            taskType="exercise",
            durationMinutes=duration,
            priority=priority,
            pet=pet or self.pet,
            preferredTime=preferred_time,
        )

    def test_flags_duplicate_start_times_as_conflicting(self):
        t1 = self._make_task("Walk", Time(8, 0))
        t2 = self._make_task("Bath", Time(8, 0))
        scheduler = Scheduler(tasks=[t1, t2], timeAvailable=60)

        conflicts = scheduler.detectConflicts()

        self.assertIn(t1, conflicts)
        self.assertIn(t2, conflicts)

    def test_getConflictWarnings_reports_duplicate_times(self):
        t1 = self._make_task("Walk", Time(8, 0))
        t2 = self._make_task("Bath", Time(8, 0))
        scheduler = Scheduler(tasks=[t1, t2], timeAvailable=60)

        warnings = scheduler.getConflictWarnings()

        self.assertEqual(len(warnings), 1)
        self.assertIn("Walk", warnings[0])
        self.assertIn("Bath", warnings[0])

    def test_adjacent_non_overlapping_tasks_do_not_conflict(self):
        t1 = self._make_task("First", Time(8, 0), duration=30)
        t2 = self._make_task("Second", Time(8, 30), duration=30)
        scheduler = Scheduler(tasks=[t1, t2], timeAvailable=60)

        self.assertEqual(scheduler.detectConflicts(), [])

    def test_completed_tasks_are_excluded_from_conflicts(self):
        t_done = self._make_task("Done", Time(8, 0), duration=30)
        t_done.markComplete()
        t_pending = self._make_task("Pending", Time(8, 0), duration=30)
        scheduler = Scheduler(tasks=[t_done, t_pending], timeAvailable=60)

        conflicts = scheduler.detectConflicts()

        self.assertNotIn(t_done, conflicts)
        self.assertNotIn(t_pending, conflicts)
        self.assertEqual(scheduler.getConflictWarnings(), [])

    def test_zero_duration_task_does_not_conflict_at_same_start_time(self):
        t_instant = self._make_task("Instant", Time(8, 0), duration=0)
        t_other = self._make_task("Other", Time(8, 0), duration=30)
        scheduler = Scheduler(tasks=[t_instant, t_other], timeAvailable=60)

        self.assertEqual(scheduler.detectConflicts(), [])

    def test_three_way_overlap_dedupes_into_single_list(self):
        t1 = self._make_task("A", Time(8, 0), duration=30)
        t2 = self._make_task("B", Time(8, 10), duration=30)
        t3 = self._make_task("C", Time(8, 20), duration=30)
        scheduler = Scheduler(tasks=[t1, t2, t3], timeAvailable=90)

        conflicts = scheduler.detectConflicts()

        self.assertEqual(len(conflicts), 3)
        self.assertIn(t1, conflicts)
        self.assertIn(t2, conflicts)
        self.assertIn(t3, conflicts)

    def test_same_pet_vs_cross_pet_conflict_labeling(self):
        other_pet = make_pet("Mittens")
        t_same_pet_a = self._make_task("Walk", Time(8, 0), duration=30, pet=self.pet)
        t_same_pet_b = self._make_task("Bath", Time(8, 0), duration=30, pet=self.pet)
        t_cross_pet_a = self._make_task("Feed", Time(9, 0), duration=30, pet=other_pet)
        t_cross_pet_b = self._make_task("Play", Time(9, 0), duration=30, pet=self.pet)

        same_pet_scheduler = Scheduler(tasks=[t_same_pet_a, t_same_pet_b], timeAvailable=60)
        cross_pet_scheduler = Scheduler(tasks=[t_cross_pet_a, t_cross_pet_b], timeAvailable=60)

        self.assertIn("[same-pet]", same_pet_scheduler.getConflictWarnings()[0])
        self.assertIn("[cross-pet]", cross_pet_scheduler.getConflictWarnings()[0])


class TestWindowWrapsPastMidnight(unittest.TestCase):
    def setUp(self):
        self.pet = make_pet()
        self.today = date.today()

    def _make_task(self, name, preferred_time, duration=30):
        return Task(
            taskName=name,
            taskType="exercise",
            durationMinutes=duration,
            priority=Priority.HIGH,
            pet=self.pet,
            preferredTime=preferred_time,
            dueDate=self.today,
        )

    def test_task_after_midnight_fits_wrapped_window(self):
        # Owner available 22:00 -> 01:00 (180 min); 00:30 falls inside that window
        t_after_midnight = self._make_task("Late-night check", Time(0, 30), duration=20)
        s = Scheduler(tasks=[t_after_midnight], timeAvailable=180, startTime=Time(22, 0))

        result = s.generatePlan()

        self.assertIn(t_after_midnight, result)
        self.assertNotIn(t_after_midnight, s.unscheduledTasks)

    def test_task_outside_wrapped_window_is_still_rejected(self):
        # Same wrapped window (22:00 -> 01:00); a midday task should not fit
        t_midday = self._make_task("Midday", Time(12, 0), duration=20)
        s = Scheduler(tasks=[t_midday], timeAvailable=180, startTime=Time(22, 0))

        result = s.generatePlan()

        self.assertNotIn(t_midday, result)
        self.assertIn(t_midday, s.unscheduledTasks)

    def test_task_exactly_at_window_boundaries_is_accepted(self):
        t_boundary = self._make_task("Boundary", Time(8, 0), duration=60)
        s = Scheduler(tasks=[t_boundary], timeAvailable=60, startTime=Time(8, 0))

        result = s.generatePlan()

        self.assertIn(t_boundary, result)


class TestExplainPlan(unittest.TestCase):
    def test_explain_plan_before_generation(self):
        scheduler = Scheduler(timeAvailable=60)
        self.assertEqual(
            scheduler.explainPlan(),
            "No plan has been generated yet. Click \"Generate Schedule\" to create one.",
        )

    def test_explain_plan_groups_by_pet_and_lists_unscheduled(self):
        pet = make_pet("Buddy")
        t_fits = Task(
            taskName="Walk", taskType="exercise", durationMinutes=20,
            priority=Priority.HIGH, pet=pet, preferredTime=Time(8, 0),
        )
        t_overflow = Task(
            taskName="Bath", taskType="grooming", durationMinutes=60,
            priority=Priority.LOW, pet=pet, preferredTime=Time(9, 0),
        )
        scheduler = Scheduler(tasks=[t_fits, t_overflow], timeAvailable=30, ownerName="Jordan")
        scheduler.generatePlan()

        explanation = scheduler.explainPlan()

        self.assertIn("Jordan's pets", explanation)
        self.assertIn("Walk", explanation)
        self.assertIn("Not scheduled", explanation)
        self.assertIn("Bath", explanation)


class TestEditTask(unittest.TestCase):
    def test_edit_task_replaces_in_scheduler_and_pet(self):
        pet = make_pet()
        scheduler = Scheduler(timeAvailable=60)
        task = make_task(pet, name="Original")
        scheduler.addTask(task)

        updated = Task(
            taskId=task.taskId,
            taskName="Updated",
            taskType=task.taskType,
            durationMinutes=task.durationMinutes,
            priority=task.priority,
            pet=pet,
            preferredTime=task.preferredTime,
        )
        scheduler.editTask(updated)

        self.assertEqual(scheduler.tasks[0].taskName, "Updated")
        self.assertEqual(pet.tasks[0].taskName, "Updated")

    def test_edit_task_with_unknown_id_is_a_no_op(self):
        pet = make_pet()
        scheduler = Scheduler(timeAvailable=60)
        task = make_task(pet, name="Original")
        scheduler.addTask(task)

        unrelated = make_task(pet, name="Unrelated")
        scheduler.editTask(unrelated)

        self.assertEqual(len(scheduler.tasks), 1)
        self.assertEqual(scheduler.tasks[0].taskName, "Original")


class TestRemoveTask(unittest.TestCase):
    def test_remove_task_drops_it_from_scheduler_and_pet(self):
        pet = make_pet()
        scheduler = Scheduler(timeAvailable=60)
        task = make_task(pet, name="Walk")
        scheduler.addTask(task)

        scheduler.removeTask(task)

        self.assertNotIn(task, scheduler.tasks)
        self.assertNotIn(task, pet.tasks)

    def test_remove_task_also_clears_it_from_cached_plan_lists(self):
        pet = make_pet()
        scheduler = Scheduler(tasks=[], timeAvailable=60)
        task = make_task(pet, name="Walk")
        scheduler.addTask(task)
        scheduler.generatePlan()
        self.assertIn(task, scheduler.dailyPlan)

        scheduler.removeTask(task)

        self.assertNotIn(task, scheduler.dailyPlan)
        self.assertNotIn(task, scheduler.tasks)

    def test_remove_unrelated_task_is_a_no_op(self):
        pet = make_pet()
        scheduler = Scheduler(timeAvailable=60)
        task = make_task(pet, name="Walk")
        scheduler.addTask(task)

        unrelated = make_task(pet, name="Feed")
        scheduler.removeTask(unrelated)

        self.assertIn(task, scheduler.tasks)
        self.assertEqual(len(scheduler.tasks), 1)


class TestJsonPersistence(unittest.TestCase):
    def setUp(self):
        fd, self.temp_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.temp_path):
            os.remove(self.temp_path)

    def test_round_trip_restores_owner_pets_and_tasks(self):
        owner, pet, task = make_owner_with_task()

        owner.save_to_json(self.temp_path)
        loaded = Owner.load_from_json(self.temp_path)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "Jordan")
        self.assertEqual(len(loaded.pets), 1)
        self.assertEqual(loaded.pets[0].name, "Buddy")
        self.assertEqual(len(loaded.scheduler.tasks), 1)
        self.assertEqual(
            loaded.scheduler.tasks[0].taskName,
            "Walk",
        )
        self.assertIs(
            loaded.scheduler.tasks[0].pet,
            loaded.pets[0],
        )

    def test_deleted_task_does_not_reappear_after_reload(self):
        owner, pet, task = make_owner_with_task()
        owner.save_to_json(self.temp_path)

        owner.scheduler.removeTask(task)
        owner.save_to_json(self.temp_path)

        loaded = Owner.load_from_json(self.temp_path)

        self.assertEqual(
            len(loaded.scheduler.tasks),
            0,
        )

    def test_load_from_json_returns_none_when_file_missing(self):
        missing_path = self.temp_path + ".does-not-exist"

        self.assertIsNone(
            Owner.load_from_json(missing_path)
        )

    def test_round_trip_preserves_completed_and_recurrence(self):
        owner, pet, task = make_owner_with_task(
            recurrence="daily",
            completed=True,
        )

        owner.save_to_json(self.temp_path)
        loaded = Owner.load_from_json(self.temp_path)

        self.assertTrue(
            loaded.scheduler.tasks[0].completed
        )
        self.assertEqual(
            loaded.scheduler.tasks[0].recurrence,
            "daily",
        )

    def test_deleted_task_does_not_reappear_after_reload(self):
        owner = Owner(name="Jordan", startTime=Time(7, 0), endTime=Time(19, 0), preferences={})
        pet = make_pet("Buddy")
        owner.addPet(pet)
        task = make_task(pet, name="Walk")
        owner.scheduler.addTask(task)
        owner.save_to_json(self.temp_path)

        owner.scheduler.removeTask(task)
        owner.save_to_json(self.temp_path)

        loaded = Owner.load_from_json(self.temp_path)
        self.assertEqual(len(loaded.scheduler.tasks), 0)

    def test_load_from_json_returns_none_when_file_missing(self):
        missing_path = self.temp_path + ".does-not-exist"
        self.assertIsNone(Owner.load_from_json(missing_path))

    def test_round_trip_preserves_completed_and_recurrence(self):
        owner = Owner(name="Jordan", startTime=Time(7, 0), endTime=Time(19, 0), preferences={})
        pet = make_pet("Buddy")
        owner.addPet(pet)
        task = make_task(pet, name="Walk")
        task.recurrence = "daily"
        task.markComplete()
        owner.scheduler.addTask(task)

        owner.save_to_json(self.temp_path)
        loaded = Owner.load_from_json(self.temp_path)

        self.assertTrue(loaded.scheduler.tasks[0].completed)
        self.assertEqual(loaded.scheduler.tasks[0].recurrence, "daily")


class TestTaskMethods(unittest.TestCase):
    def test_update_task_changes_existing_field(self):
        pet = make_pet()
        task = make_task(pet)

        task.updateTask("durationMinutes", 45)

        self.assertEqual(task.durationMinutes, 45)

    def test_update_task_rejects_nonexistent_field(self):
        pet = make_pet()
        task = make_task(pet)

        with self.assertRaisesRegex(
            ValueError,
            "notARealField",
        ):
            task.updateTask("notARealField", 123)

        self.assertFalse(
            hasattr(task, "notARealField")
        )

    def test_update_task_rejects_protected_identity_field(self):
        pet = make_pet()
        task = make_task(pet)
        original_id = task.taskId

        with self.assertRaisesRegex(
            ValueError,
            "taskId",
        ):
            task.updateTask(
                "taskId",
                "replacement-id",
            )

        self.assertEqual(
            task.taskId,
            original_id,
        )

    def test_update_task_rejects_relationship_field(self):
        pet = make_pet("Mochi")
        other_pet = make_pet("Luna")
        task = make_task(pet)

        with self.assertRaisesRegex(
            ValueError,
            "pet",
        ):
            task.updateTask("pet", other_pet)

        self.assertIs(task.pet, pet)

    def test_get_task_summary_includes_notes_when_present(self):
        pet = make_pet()
        task = make_task(pet)
        task.notes = "Give extra treat"

        summary = task.getTaskSummary()

        self.assertIn(
            "Notes: Give extra treat",
            summary,
        )

    def test_get_task_summary_omits_recurrence_suffix_when_none(self):
        pet = make_pet()
        task = make_task(pet)

        self.assertNotIn(
            "(none)",
            task.getTaskSummary(),
        )

    def test_get_task_summary_includes_recurrence_suffix_when_recurring(self):
        pet = make_pet()
        task = make_task(pet)
        task.recurrence = "daily"

        self.assertIn(
            "(daily)",
            task.getTaskSummary(),
        )


class TestPetMethods(unittest.TestCase):
    def test_update_pet_changes_allowed_field(self):
        pet = make_pet()

        pet.updatePet("name", "Luna")

        self.assertEqual(pet.name, "Luna")

    def test_update_pet_rejects_unknown_field(self):
        pet = make_pet()

        with self.assertRaisesRegex(
            ValueError,
            "favoriteColor",
        ):
            pet.updatePet(
                "favoriteColor",
                "purple",
            )

        self.assertFalse(
            hasattr(pet, "favoriteColor")
        )

    def test_update_pet_rejects_protected_tasks_field(self):
        pet = make_pet()
        original_tasks = pet.tasks

        with self.assertRaisesRegex(
            ValueError,
            "tasks",
        ):
            pet.updatePet("tasks", [])

        self.assertIs(
            pet.tasks,
            original_tasks,
        )

    def test_add_care_need_appends_to_list(self):
        pet = make_pet()

        pet.addCareNeed("daily medication")

        self.assertIn(
            "daily medication",
            pet.careNeeds,
        )

    def test_get_pet_summary_defaults_when_empty(self):
        pet = make_pet()
        pet.medication = ""

        summary = pet.getPetSummary()

        self.assertIn(
            "Medication: none",
            summary,
        )
        self.assertIn(
            "Care needs: none",
            summary,
        )

    def test_get_pet_summary_lists_multiple_care_needs(self):
        pet = make_pet()
        pet.addCareNeed("meds")
        pet.addCareNeed("grooming")

        summary = pet.getPetSummary()

        self.assertIn(
            "Care needs: meds, grooming",
            summary,
        )



class TestOwnerMethods(unittest.TestCase):
    def test_add_pet_appends_to_pets_list(self):
        owner = make_owner()
        pet = make_pet()

        owner.addPet(pet)

        self.assertIn(pet, owner.pets)

    def test_update_preferences_merges_dict(self):
        owner = make_owner(
            preferences={"morning": "walk"}
        )

        owner.updatePreferences({
            "evening": "feed",
        })

        self.assertEqual(
            owner.preferences,
            {
                "morning": "walk",
                "evening": "feed",
            },
        )

    def test_get_available_time_matches_window(self):
        owner = make_owner()

        self.assertEqual(
            owner.getAvailableTime(),
            12 * 60,
        )

    def test_available_minutes_clamped_to_zero_when_start_after_end(self):
        owner = make_owner(
            start_time=Time(19, 0),
            end_time=Time(7, 0),
        )

        self.assertEqual(
            owner.getAvailableTime(),
            0,
        )

if __name__ == "__main__":
    unittest.main()
