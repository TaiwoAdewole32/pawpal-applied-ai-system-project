from datetime import date, time

import pytest

from pawpal_system import Owner, Pet, Priority, Task
from sentinel_service import PawPalSentinel, WorkflowStatus


class QueueAIClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_json(self, system_prompt, user_payload):
        self.calls.append((system_prompt, user_payload))
        if not self.responses:
            raise AssertionError("Unexpected extra AI call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_owner(*, conflict=True, fixed_pair=False):
    owner = Owner("Jordan", time(7), time(19), {})
    dog = Pet("Mochi", "Dog", "", 2, "Kibble", "none", 5)
    owner.addPet(dog)

    if fixed_pair:
        first = Task(
            taskId="med-1",
            taskName="Medication",
            taskType="medication",
            durationMinutes=30,
            priority=Priority.HIGH,
            pet=dog,
            preferredTime=time(8),
            dueDate=date.today(),
        )
        second = Task(
            taskId="vet-1",
            taskName="Vet Appointment",
            taskType="vet appointment",
            durationMinutes=30,
            priority=Priority.HIGH,
            pet=dog,
            preferredTime=time(8),
            dueDate=date.today(),
        )
    else:
        first = Task(
            taskId="med-1",
            taskName="Medication",
            taskType="medication",
            durationMinutes=30,
            priority=Priority.HIGH,
            pet=dog,
            preferredTime=time(8),
            dueDate=date.today(),
        )
        second = Task(
            taskId="walk-1",
            taskName="Walk",
            taskType="walk",
            durationMinutes=30,
            priority=Priority.MEDIUM,
            pet=dog,
            preferredTime=time(8 if conflict else 9),
            dueDate=date.today(),
        )

    owner.scheduler.addTask(first)
    owner.scheduler.addTask(second)
    return owner, first, second


def critic_needs(task_ids):
    return {
        "status": "needs_revision",
        "summary": "The tasks overlap.",
        "issues": [
            {
                "issue_type": "schedule_conflict",
                "task_ids": list(task_ids),
                "severity": "high",
                "explanation": "The supplied deterministic evidence shows an overlap.",
                "rule_sections": [],
            }
        ],
        "confidence": 0.95,
    }


def critic_clear():
    return {
        "status": "no_change_needed",
        "summary": "No supported scheduling issue was found.",
        "issues": [],
        "confidence": 0.9,
    }


def repair_move(new_time):
    return {
        "proposed_changes": [
            {
                "task_id": "walk-1",
                "action": "move",
                "original_time": "08:00",
                "new_time": new_time,
                "reason": "Move the flexible walk.",
            }
        ],
        "summary": "Move the walk only.",
    }


def no_rules(query, path, top_k):
    return []


def test_conflict_free_skips_repair():
    owner, _, _ = make_owner(conflict=False)
    client = QueueAIClient([critic_clear()])
    run = PawPalSentinel(client, rule_retriever=no_rules).review_plan(owner)

    assert run.status is WorkflowStatus.NO_REPAIR_NEEDED
    assert len(client.calls) == 1
    assert run.repair_attempts == ()


def test_valid_proposal_waits_for_approval_without_mutation():
    owner, _, walk = make_owner()
    original = walk.preferredTime
    client = QueueAIClient([critic_needs(("med-1", "walk-1")), repair_move("09:00")])

    run = PawPalSentinel(client, rule_retriever=no_rules).review_plan(owner)

    assert run.status is WorkflowStatus.AWAITING_OWNER_APPROVAL
    assert run.can_approve is True
    assert walk.preferredTime == original
    assert len(run.repair_attempts) == 1
    assert run.final_validation.valid is True
    assert run.validated_changes[0].new_time == "09:00"


def test_invalid_first_attempt_gets_one_revision_then_succeeds():
    owner, _, walk = make_owner()
    client = QueueAIClient(
        [
            critic_needs(("med-1", "walk-1")),
            repair_move("20:00"),
            repair_move("09:00"),
        ]
    )

    run = PawPalSentinel(client, rule_retriever=no_rules).review_plan(owner)

    assert run.status is WorkflowStatus.AWAITING_OWNER_APPROVAL
    assert len(run.repair_attempts) == 2
    assert run.repair_attempts[0].validation_result.valid is False
    assert run.repair_attempts[1].validation_result.valid is True
    assert walk.preferredTime == time(8)

    revision_payload = client.calls[2][1]
    assert revision_payload["repair_mode"] == "revision"
    assert revision_payload["revision_context"]["rejected_proposed_changes"][0]["new_time"] == "20:00"
    assert any("outside owner availability" in error for error in revision_payload["revision_context"]["validator_errors"])


def test_two_invalid_attempts_stop_without_third_call():
    owner, _, walk = make_owner()
    client = QueueAIClient(
        [
            critic_needs(("med-1", "walk-1")),
            repair_move("20:00"),
            repair_move("21:00"),
        ]
    )

    run = PawPalSentinel(client, rule_retriever=no_rules).review_plan(owner)

    assert run.status is WorkflowStatus.HUMAN_REVIEW_REQUIRED
    assert len(run.repair_attempts) == 2
    assert len(client.calls) == 3
    assert walk.preferredTime == time(8)


def test_unresolved_no_op_move_is_rejected_and_revised():
    owner, _, _ = make_owner()
    client = QueueAIClient(
        [
            critic_needs(("med-1", "walk-1")),
            repair_move("08:00"),
            repair_move("09:00"),
        ]
    )

    run = PawPalSentinel(client, rule_retriever=no_rules).review_plan(owner)

    assert run.status is WorkflowStatus.AWAITING_OWNER_APPROVAL
    first = run.repair_attempts[0].validation_result
    assert first.valid is False
    assert first.checks["reviewed_conflicts_resolved"] is False
    assert any("does not resolve" in error for error in first.errors)


def test_two_fixed_tasks_defer_to_human_review():
    owner, _, _ = make_owner(fixed_pair=True)
    critic = critic_needs(("med-1", "vet-1"))
    repair = {
        "proposed_changes": [
            {
                "task_id": "med-1",
                "action": "defer_for_review",
                "original_time": "08:00",
                "new_time": None,
                "reason": "Fixed conflict requires owner review.",
            },
            {
                "task_id": "vet-1",
                "action": "defer_for_review",
                "original_time": "08:00",
                "new_time": None,
                "reason": "Fixed conflict requires owner review.",
            },
        ],
        "summary": "Owner review required.",
    }
    client = QueueAIClient([critic, repair])

    run = PawPalSentinel(client, rule_retriever=no_rules).review_plan(owner)

    assert run.status is WorkflowStatus.HUMAN_REVIEW_REQUIRED
    assert run.can_approve is False
    assert run.final_validation.valid is True


def test_malformed_critic_is_controlled_invalid_ai_output():
    owner, _, _ = make_owner()
    client = QueueAIClient(["not-json"])

    run = PawPalSentinel(client, rule_retriever=no_rules).review_plan(owner)

    assert run.status is WorkflowStatus.INVALID_AI_OUTPUT
    assert len(client.calls) == 1


def test_retrieval_exception_degrades_safely():
    owner, _, _ = make_owner(conflict=True)
    client = QueueAIClient([
        critic_needs(("med-1", "walk-1")),
        repair_move("09:00"),
    ])

    def broken_retriever(query, path, top_k):
        raise OSError("missing")

    run = PawPalSentinel(client, rule_retriever=broken_retriever).review_plan(owner)

    assert run.status is WorkflowStatus.AWAITING_OWNER_APPROVAL
    assert run.retrieved_rules == ()
    assert run.warnings


def test_invalid_owner_returns_failed_instead_of_crashing():
    client = QueueAIClient([])
    run = PawPalSentinel(client, rule_retriever=no_rules).review_plan(None)
    assert run.status is WorkflowStatus.FAILED
    assert client.calls == []


def test_critic_cannot_ignore_deterministic_conflict():
    owner, _, _ = make_owner(conflict=True)
    client = QueueAIClient([critic_clear()])

    run = PawPalSentinel(client, rule_retriever=no_rules).review_plan(owner)

    assert run.status is WorkflowStatus.INVALID_AI_OUTPUT
    assert len(client.calls) == 1


def test_hallucinated_conflict_does_not_reach_repair():
    owner, _, _ = make_owner(conflict=False)
    client = QueueAIClient([critic_needs(("med-1", "walk-1"))])

    run = PawPalSentinel(client, rule_retriever=no_rules).review_plan(owner)

    assert run.status is WorkflowStatus.NO_REPAIR_NEEDED
    assert len(client.calls) == 1
    assert run.warnings