"""Tests for retriever.py (Phase 3.5).

retrieve_rules() is exercised directly with hand-written queries, and
build_retrieval_query() is exercised with real ScheduleSnapshot/TaskSnapshot/
Conflict evidence so the two are proven to work together the way
sentinel_service (Phase 5) will eventually call them.
"""

import os
import tempfile
import unittest

from schedule_validator import Conflict
from sentinel_models import ScheduleSnapshot, TaskSnapshot
from retriever import (
    MAX_CONTENT_CHARS,
    RetrievedRule,
    build_retrieval_query,
    retrieve_rules,
)


def make_task_snapshot(
    task_id, task_name="Task", task_type="walk", duration_minutes=20,
    priority="medium", pet_id="pet-1", pet_name="Pet",
    preferred_time="08:00", recurrence="none", due_date="2026-07-28",
    flexibility="flexible",
):
    return TaskSnapshot(
        task_id=task_id, task_name=task_name, task_type=task_type,
        duration_minutes=duration_minutes, priority=priority,
        pet_id=pet_id, pet_name=pet_name, preferred_time=preferred_time,
        recurrence=recurrence, due_date=due_date, flexibility=flexibility,
    )


def make_snapshot(tasks, unscheduled=(), version="v1"):
    return ScheduleSnapshot(
        owner_name="Owner", availability_start="07:00", availability_end="20:00",
        tasks=tuple(tasks), unscheduled_task_ids=tuple(unscheduled), version=version,
    )


# --- retrieve_rules(): focused keyword retrieval -------------------------------

class TestFocusedRetrieval(unittest.TestCase):
    def test_medication_conflict_retrieves_medication_tasks(self):
        results = retrieve_rules("medication fixed dosage conflict flexible task")

        self.assertTrue(results)
        self.assertEqual(results[0].section, "Medication Tasks")

    def test_move_a_walk_retrieves_walks_play_and_grooming(self):
        results = retrieve_rules("walk grooming play flexible move availability window")

        self.assertTrue(results)
        self.assertEqual(results[0].section, "Walks, Play and Grooming")

    def test_feeding_adjustment_retrieves_feeding_tasks(self):
        results = retrieve_rules("feeding preferred adjustment approve small")

        self.assertTrue(results)
        self.assertEqual(results[0].section, "Feeding Tasks")

    def test_veterinarian_conflict_retrieves_veterinarian_appointments(self):
        results = retrieve_rules("veterinarian appointment fixed replace shorten")

        self.assertTrue(results)
        self.assertEqual(results[0].section, "Veterinarian Appointments")

    def test_generic_overlap_retrieves_general_scheduling(self):
        results = retrieve_rules("general scheduling overlapping duration recurrence unchanged")

        self.assertTrue(results)
        self.assertEqual(results[0].section, "General Scheduling")

    def test_unrelated_query_does_not_return_random_sections(self):
        results = retrieve_rules("astronomy telescope orbit galaxy spaceship")

        self.assertEqual(results, [])

    def test_heading_stays_attached_to_its_own_content(self):
        results = retrieve_rules("veterinarian appointment fixed replace shorten")

        top = results[0]
        self.assertEqual(top.section, "Veterinarian Appointments")
        self.assertIn("Veterinarian", top.content + top.section)
        self.assertNotIn("Medication", top.content)


# --- Guardrails: empty / malformed input ----------------------------------------

class TestGuardrails(unittest.TestCase):
    def test_empty_query_returns_empty_list(self):
        self.assertEqual(retrieve_rules(""), [])

    def test_whitespace_only_query_returns_empty_list(self):
        self.assertEqual(retrieve_rules("   \n\t "), [])

    def test_none_query_handled_safely(self):
        self.assertEqual(retrieve_rules(None), [])

    def test_list_query_handled_safely(self):
        self.assertEqual(retrieve_rules(["medication", "conflict"]), [])

    def test_dict_query_handled_safely(self):
        self.assertEqual(retrieve_rules({"query": "medication"}), [])

    def test_int_query_handled_safely(self):
        self.assertEqual(retrieve_rules(42), [])

    def test_bool_query_handled_safely(self):
        self.assertEqual(retrieve_rules(True), [])

    def test_negative_top_k_returns_no_results(self):
        self.assertEqual(retrieve_rules("medication conflict", top_k=-1), [])

    def test_zero_top_k_returns_no_results(self):
        self.assertEqual(retrieve_rules("medication conflict", top_k=0), [])

    def test_large_top_k_is_capped_at_three(self):
        results = retrieve_rules(
            "medication veterinarian feeding walk general conflict flexible fixed",
            top_k=100,
        )
        self.assertLessEqual(len(results), 3)

    def test_missing_rules_file_handled_safely(self):
        self.assertEqual(retrieve_rules("medication conflict", rules_path="data/does_not_exist.md"), [])

    def test_results_are_retrieved_rule_instances_with_source_names(self):
        results = retrieve_rules("medication fixed dosage conflict")

        self.assertTrue(all(isinstance(r, RetrievedRule) for r in results))
        self.assertTrue(all(r.section for r in results))


# --- Determinism -----------------------------------------------------------------

class TestDeterminism(unittest.TestCase):
    def test_results_are_deterministic_for_equal_scores(self):
        first = retrieve_rules("owner window time")
        second = retrieve_rules("owner window time")

        self.assertEqual(first, second)


# --- build_retrieval_query(): evidence-driven query construction ----------------

class TestBuildRetrievalQuery(unittest.TestCase):
    def test_medication_conflict_evidence_retrieves_medication_tasks(self):
        med = make_task_snapshot("t1", task_type="medication", flexibility="fixed")
        walk = make_task_snapshot("t2", task_type="walk", flexibility="flexible")
        snapshot = make_snapshot([med, walk])

        query = build_retrieval_query(snapshot, conflicts=[Conflict("t1", "t2")])
        results = retrieve_rules(query)

        self.assertTrue(query)
        self.assertTrue(results)
        self.assertEqual(results[0].section, "Medication Tasks")

    def test_veterinarian_conflict_evidence_retrieves_veterinarian_appointments(self):
        vet = make_task_snapshot("t1", task_type="veterinarian appointment", flexibility="fixed")
        groom = make_task_snapshot("t2", task_type="grooming", flexibility="flexible")
        snapshot = make_snapshot([vet, groom])

        query = build_retrieval_query(snapshot, conflicts=[Conflict("t1", "t2")])
        results = retrieve_rules(query)

        self.assertEqual(results[0].section, "Veterinarian Appointments")

    def test_feeding_conflict_evidence_retrieves_feeding_tasks(self):
        feed = make_task_snapshot("t1", task_type="feeding", flexibility="preferred")
        walk = make_task_snapshot("t2", task_type="walk", flexibility="flexible")
        snapshot = make_snapshot([feed, walk])

        query = build_retrieval_query(snapshot, conflicts=[Conflict("t1", "t2")])
        results = retrieve_rules(query)

        self.assertEqual(results[0].section, "Feeding Tasks")

    def test_unscheduled_walk_evidence_retrieves_walks_play_and_grooming(self):
        play = make_task_snapshot("t1", task_type="play", flexibility="flexible")
        snapshot = make_snapshot([play], unscheduled=("t1",))

        query = build_retrieval_query(
            snapshot, unscheduled_task_ids=("t1",), availability_violation=True,
        )
        results = retrieve_rules(query)

        self.assertEqual(results[0].section, "Walks, Play and Grooming")

    def test_conflict_free_snapshot_with_no_evidence_produces_no_query(self):
        walk = make_task_snapshot("t1", task_type="walk")
        snapshot = make_snapshot([walk])

        query = build_retrieval_query(snapshot)

        self.assertEqual(query, "")
        self.assertEqual(retrieve_rules(query), [])

    def test_query_ignores_conflict_tuple_shape_not_just_conflict_dataclass(self):
        med = make_task_snapshot("t1", task_type="medication", flexibility="fixed")
        walk = make_task_snapshot("t2", task_type="walk", flexibility="flexible")
        snapshot = make_snapshot([med, walk])

        query = build_retrieval_query(snapshot, conflicts=[("t1", "t2")])

        self.assertIn("medication", query)
        self.assertIn("walk", query)

    def test_query_never_includes_task_notes_style_free_text(self):
        # TaskSnapshot has no notes field at all (Phase 2.7) -- there is no
        # attribute build_retrieval_query could accidentally read free text
        # from, so this asserts the shape rather than a runtime filter.
        med = make_task_snapshot("t1", task_type="medication", flexibility="fixed")
        self.assertFalse(hasattr(med, "notes"))


# --- Path-safety guardrail (Phase 3.4) ------------------------------------------

class TestPathSafety(unittest.TestCase):
    def test_default_rules_path_still_works(self):
        results = retrieve_rules("medication fixed dosage conflict")
        self.assertTrue(results)

    def test_path_traversal_string_rejected(self):
        self.assertEqual(
            retrieve_rules("medication conflict", rules_path="../../etc/passwd"), []
        )

    def test_absolute_path_outside_data_dir_rejected(self):
        outside = os.path.join(tempfile.gettempdir(), "not_care_rules.md")
        self.assertEqual(retrieve_rules("medication conflict", rules_path=outside), [])

    def test_non_string_rules_path_none_rejected(self):
        self.assertEqual(retrieve_rules("medication conflict", rules_path=None), [])

    def test_non_string_rules_path_int_rejected(self):
        self.assertEqual(retrieve_rules("medication conflict", rules_path=123), [])

    def test_non_string_rules_path_list_rejected(self):
        self.assertEqual(retrieve_rules("medication conflict", rules_path=["x"]), [])


# --- top_k type robustness --------------------------------------------------------

class TestTopKTypeRobustness(unittest.TestCase):
    def test_float_top_k_handled_safely(self):
        self.assertEqual(retrieve_rules("medication conflict", top_k=2.5), [])

    def test_string_top_k_handled_safely(self):
        self.assertEqual(retrieve_rules("medication conflict", top_k="3"), [])

    def test_none_top_k_handled_safely(self):
        self.assertEqual(retrieve_rules("medication conflict", top_k=None), [])


# --- Section length capping (Phase 3.4 "cap section length") ---------------------

class TestSectionLengthCapping(unittest.TestCase):
    def setUp(self):
        fd, self.temp_path = tempfile.mkstemp(suffix=".md", dir="data")
        os.close(fd)
        long_content = "medication conflict repeat " * 60  # well over MAX_CONTENT_CHARS
        with open(self.temp_path, "w", encoding="utf-8") as f:
            f.write(f"# Medication Tasks\n{long_content}\n\n# Short Section\nfeeding preferred\n")

    def tearDown(self):
        os.remove(self.temp_path)

    def test_long_section_is_truncated_to_max_content_chars(self):
        rel_path = os.path.relpath(self.temp_path)
        results = retrieve_rules("medication conflict", rules_path=rel_path)

        self.assertTrue(results)
        top = results[0]
        self.assertEqual(top.section, "Medication Tasks")
        self.assertTrue(len(top.content) <= MAX_CONTENT_CHARS + 3)
        self.assertTrue(top.content.endswith("..."))

    def test_short_section_is_returned_unmodified(self):
        rel_path = os.path.relpath(self.temp_path)
        results = retrieve_rules("feeding preferred", rules_path=rel_path)

        matching = [r for r in results if r.section == "Short Section"]
        self.assertTrue(matching)
        self.assertEqual(matching[0].content, "feeding preferred")


# --- General Scheduling is never a forced default --------------------------------

class TestGeneralSchedulingFallbackNotForced(unittest.TestCase):
    def test_stopword_only_query_returns_nothing_not_general_scheduling(self):
        results = retrieve_rules("the a is are")
        self.assertEqual(results, [])

    def test_greeting_query_returns_nothing_not_general_scheduling(self):
        results = retrieve_rules("hello there how are you")
        self.assertEqual(results, [])


# --- build_retrieval_query malformed-evidence robustness --------------------------

class TestBuildRetrievalQueryRobustness(unittest.TestCase):
    def setUp(self):
        self.walk = make_task_snapshot("t1", task_type="walk", flexibility="flexible")
        self.snapshot = make_snapshot([self.walk])

    def test_conflicts_none_does_not_crash(self):
        self.assertEqual(build_retrieval_query(self.snapshot, conflicts=None), "")

    def test_unscheduled_task_ids_none_does_not_crash(self):
        self.assertEqual(
            build_retrieval_query(self.snapshot, unscheduled_task_ids=None), ""
        )

    def test_issue_labels_none_does_not_crash(self):
        self.assertEqual(build_retrieval_query(self.snapshot, issue_labels=None), "")

    def test_malformed_conflict_entry_is_skipped_not_fatal(self):
        query = build_retrieval_query(
            self.snapshot,
            conflicts=[("only-one-id",), Conflict("t1", "t1")],
        )
        self.assertIn("walk", query)
        self.assertIn("flexible", query)

    def test_conflict_referencing_unknown_task_id_does_not_crash(self):
        query = build_retrieval_query(
            self.snapshot, conflicts=[Conflict("unknown-id", "t1")]
        )
        self.assertIn("walk", query)
        self.assertIn("conflict", query)


if __name__ == "__main__":
    unittest.main()
