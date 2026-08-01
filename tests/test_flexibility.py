import unittest
from datetime import time as Time, date

from pawpal_system import Flexibility, Pet, Priority, Task, resolve_flexibility


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


def make_task(pet, task_type="walk", flexibility=None, name="Task"):
    kwargs = dict(
        taskName=name,
        taskType=task_type,
        durationMinutes=20,
        priority=Priority.MEDIUM,
        pet=pet,
        preferredTime=Time(8, 0),
    )
    if flexibility is not None:
        kwargs["flexibility"] = flexibility
    return Task(**kwargs)


class TestResolveFlexibilityDefaults(unittest.TestCase):
    def test_medication_defaults_to_fixed(self):
        self.assertEqual(resolve_flexibility("medication", None), Flexibility.FIXED)

    def test_veterinarian_defaults_to_fixed(self):
        self.assertEqual(resolve_flexibility("veterinarian", None), Flexibility.FIXED)
        self.assertEqual(resolve_flexibility("vet appointment", None), Flexibility.FIXED)

    def test_feeding_defaults_to_preferred(self):
        self.assertEqual(resolve_flexibility("feed", None), Flexibility.PREFERRED)
        self.assertEqual(resolve_flexibility("feeding", None), Flexibility.PREFERRED)

    def test_unknown_type_defaults_to_flexible(self):
        self.assertEqual(resolve_flexibility("walk", None), Flexibility.FLEXIBLE)
        self.assertEqual(resolve_flexibility("something-made-up", None), Flexibility.FLEXIBLE)

    def test_normalization_is_case_and_whitespace_insensitive(self):
        self.assertEqual(resolve_flexibility("  MEDICATION  ", None), Flexibility.FIXED)


class TestResolveFlexibilityProtection(unittest.TestCase):
    def test_medication_cannot_be_downgraded_to_flexible(self):
        self.assertEqual(resolve_flexibility("medication", "flexible"), Flexibility.FIXED)
        self.assertEqual(resolve_flexibility("medication", Flexibility.FLEXIBLE), Flexibility.FIXED)

    def test_appointment_cannot_be_downgraded(self):
        self.assertEqual(resolve_flexibility("appointment", "preferred"), Flexibility.FIXED)


class TestResolveFlexibilityValidation(unittest.TestCase):
    def test_valid_string_values_accepted(self):
        self.assertEqual(resolve_flexibility("walk", "fixed"), Flexibility.FIXED)
        self.assertEqual(resolve_flexibility("walk", "preferred"), Flexibility.PREFERRED)
        self.assertEqual(resolve_flexibility("walk", "flexible"), Flexibility.FLEXIBLE)

    def test_flexibility_enum_value_accepted(self):
        self.assertEqual(resolve_flexibility("walk", Flexibility.PREFERRED), Flexibility.PREFERRED)

    def test_invalid_string_raises_value_error_with_allowed_values(self):
        with self.assertRaisesRegex(ValueError, "Allowed values: fixed, preferred, flexible"):
            resolve_flexibility("walk", "sometimes")

    def test_list_rejected(self):
        with self.assertRaises(TypeError):
            resolve_flexibility("walk", ["flexible"])

    def test_dict_rejected(self):
        with self.assertRaises(TypeError):
            resolve_flexibility("walk", {"value": "flexible"})

    def test_integer_rejected(self):
        with self.assertRaises(TypeError):
            resolve_flexibility("walk", 1)

    def test_boolean_rejected(self):
        with self.assertRaises(TypeError):
            resolve_flexibility("walk", True)


class TestTaskConstructionWithFlexibility(unittest.TestCase):
    def test_existing_style_construction_without_flexibility_still_works(self):
        pet = make_pet()
        task = Task(
            taskName="Walk",
            taskType="walk",
            durationMinutes=20,
            priority=Priority.MEDIUM,
            pet=pet,
            preferredTime=Time(8, 0),
        )
        self.assertEqual(task.flexibility, Flexibility.FLEXIBLE)

    def test_medication_task_is_forced_fixed_on_construction(self):
        pet = make_pet()
        task = make_task(pet, task_type="medication")
        self.assertEqual(task.flexibility, Flexibility.FIXED)

    def test_medication_task_stays_fixed_even_if_flexible_requested(self):
        pet = make_pet()
        task = make_task(pet, task_type="medication", flexibility="flexible")
        self.assertEqual(task.flexibility, Flexibility.FIXED)

    def test_feeding_task_defaults_to_preferred(self):
        pet = make_pet()
        task = make_task(pet, task_type="feed")
        self.assertEqual(task.flexibility, Flexibility.PREFERRED)

    def test_invalid_flexibility_string_raises_on_construction(self):
        pet = make_pet()
        with self.assertRaises(ValueError):
            make_task(pet, task_type="walk", flexibility="sometimes")


class TestFlexibilityPersistence(unittest.TestCase):
    def test_round_trips_through_to_dict_and_from_dict(self):
        pet = make_pet()
        task = make_task(pet, task_type="walk", flexibility="preferred")

        data = task.to_dict()
        self.assertEqual(data["flexibility"], "preferred")

        rebuilt = Task.from_dict(data, pet)
        self.assertEqual(rebuilt.flexibility, Flexibility.PREFERRED)

    def test_missing_flexibility_key_in_dict_resolves_safely(self):
        pet = make_pet()
        old_style_data = {
            "taskId": "old-1",
            "taskName": "Legacy Walk",
            "taskType": "walk",
            "durationMinutes": 20,
            "priority": "medium",
            "petId": pet.petId,
            "preferredTime": "08:00:00",
            "recurrence": "none",
            "dueDate": date.today().isoformat(),
            "completed": False,
            "notes": "",
        }
        rebuilt = Task.from_dict(old_style_data, pet)
        self.assertEqual(rebuilt.flexibility, Flexibility.FLEXIBLE)

    def test_missing_flexibility_key_forces_medication_fixed(self):
        pet = make_pet()
        old_style_data = {
            "taskId": "old-2",
            "taskName": "Legacy Medication",
            "taskType": "medication",
            "durationMinutes": 5,
            "priority": "high",
            "petId": pet.petId,
            "preferredTime": "09:00:00",
            "recurrence": "none",
            "dueDate": date.today().isoformat(),
            "completed": False,
            "notes": "",
        }
        rebuilt = Task.from_dict(old_style_data, pet)
        self.assertEqual(rebuilt.flexibility, Flexibility.FIXED)

    def test_spawn_next_preserves_flexibility(self):
        pet = make_pet()
        task = make_task(pet, task_type="feed", flexibility="preferred")
        task.recurrence = "daily"

        next_task = task._spawn_next()

        self.assertIsNotNone(next_task)
        self.assertEqual(next_task.flexibility, Flexibility.PREFERRED)


class TestUpdateTaskFlexibility(unittest.TestCase):
    def test_update_task_flexibility_to_valid_value(self):
        pet = make_pet()
        task = make_task(pet, task_type="walk")

        task.updateTask("flexibility", "preferred")

        self.assertEqual(task.flexibility, Flexibility.PREFERRED)

    def test_update_task_cannot_weaken_medication_task(self):
        pet = make_pet()
        task = make_task(pet, task_type="medication")

        task.updateTask("flexibility", "flexible")

        self.assertEqual(task.flexibility, Flexibility.FIXED)


if __name__ == "__main__":
    unittest.main()
