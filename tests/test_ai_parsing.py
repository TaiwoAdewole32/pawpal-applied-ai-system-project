from __future__ import annotations

import copy
import json
from datetime import date, time
from types import SimpleNamespace

import pytest

from ai_client import (
    AIConfigError,
    AIResponseParseError,
    FakeAIClient,
    GeminiAIClient,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_MODEL_RESPONSE_CHARS,
    _redact_secret,
    parse_model_json_object,
)
from pawpal_system import Owner, Pet, Priority, Task
from plan_critic import (
    PLAN_CRITIC_SYSTEM_PROMPT,
    PlanCritic,
    PlanCriticError,
    PlanCriticInputError,
)
from repair_agent import (
    REPAIR_AGENT_SYSTEM_PROMPT,
    REPAIR_PROMPT_VERSION,
    RepairAgent,
    RepairAgentError,
    RepairAgentInputError,
)
from schedule_validator import ScheduleValidator
from sentinel_models import (
    AIResponseValidationError,
    CriticResult,
    CriticStatus,
    IssueType,
    MAX_CRITIC_ISSUES,
    MAX_EXPLANATION_LENGTH,
    MAX_REASON_LENGTH,
    MAX_REPAIR_CHANGES,
    MAX_RULE_SECTIONS_PER_ISSUE,
    MAX_SUMMARY_LENGTH,
    MAX_TASK_IDS_PER_ISSUE,
    RepairResult,
    ScheduleSnapshot,
    TaskSnapshot,
    build_schedule_snapshot,
)


# ---------------------------------------------------------------------------
# Phase 4.7: raw model response parser
# ---------------------------------------------------------------------------


def test_parse_accepts_already_decoded_object():
    payload = {"status": "ok", "items": [1, True, None]}
    assert parse_model_json_object(payload) == payload


def test_parse_accepts_plain_json_object_string():
    assert parse_model_json_object('{"status":"ok"}') == {"status": "ok"}


@pytest.mark.parametrize("language", ["json", "JSON", "Json", ""])
def test_parse_accepts_only_known_surrounding_json_fences(language):
    opening = f"```{language}" if language else "```"
    raw = f'{opening}\n{{"status":"ok"}}\n```'
    assert parse_model_json_object(raw) == {"status": "ok"}


@pytest.mark.parametrize("raw", ["", "   ", "\n\t"])
def test_parse_rejects_empty_response(raw):
    with pytest.raises(AIResponseParseError, match="empty"):
        parse_model_json_object(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        '{"status":}',
        "not json",
        '{"status":"ok",}',
        '{"status":"ok"} trailing',
        'prefix {"status":"ok"}',
        '{"status":"ok"}\n{"second":true}',
    ],
)
def test_parse_rejects_malformed_or_prose_wrapped_json(raw):
    with pytest.raises(AIResponseParseError):
        parse_model_json_object(raw)


@pytest.mark.parametrize(
    "raw",
    [
        '```python\n{"status":"ok"}\n```',
        '```javascript\n{"status":"ok"}\n```',
        '```json\n{"status":"ok"}',
        '```json {"status":"ok"} ```',
        '```json\n{"status":"ok"}\n```\nextra',
    ],
)
def test_parse_rejects_unsupported_or_incomplete_fences(raw):
    with pytest.raises(AIResponseParseError, match="fence"):
        parse_model_json_object(raw)


@pytest.mark.parametrize("raw", ["[]", "null", "true", "42", '"text"'])
def test_parse_requires_json_object_root(raw):
    with pytest.raises(AIResponseParseError, match="root"):
        parse_model_json_object(raw)


@pytest.mark.parametrize("raw", [None, [], (), 7, True, object()])
def test_parse_rejects_non_string_non_dict_inputs(raw):
    with pytest.raises(AIResponseParseError, match="must be"):
        parse_model_json_object(raw)


def test_parse_rejects_duplicate_json_keys():
    with pytest.raises(AIResponseParseError, match="duplicate JSON key 'status'"):
        parse_model_json_object('{"status":"one","status":"two"}')


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_parse_rejects_non_standard_numeric_constants(constant):
    with pytest.raises(AIResponseParseError, match="non-standard numeric"):
        parse_model_json_object(f'{{"confidence":{constant}}}')


def test_parse_rejects_non_finite_float_in_predecoded_dict():
    with pytest.raises(AIResponseParseError, match="non-finite"):
        parse_model_json_object({"confidence": float("nan")})


def test_parse_rejects_non_string_object_key():
    with pytest.raises(AIResponseParseError, match="keys must be strings"):
        parse_model_json_object({1: "value"})


def test_parse_rejects_non_json_nested_python_value():
    with pytest.raises(AIResponseParseError, match="non-JSON value"):
        parse_model_json_object({"value": (1, 2)})


def test_parse_rejects_oversized_string_response():
    raw = '{"text":"' + ("x" * MAX_MODEL_RESPONSE_CHARS) + '"}'
    with pytest.raises(AIResponseParseError, match="maximum length"):
        parse_model_json_object(raw)


def test_parse_rejects_oversized_predecoded_text_content():
    with pytest.raises(AIResponseParseError, match="safe size limit"):
        parse_model_json_object({"text": "x" * (MAX_MODEL_RESPONSE_CHARS + 1)})


def test_parse_rejects_excessive_json_depth():
    value: object = "leaf"
    for _ in range(MAX_JSON_DEPTH + 2):
        value = {"next": value}
    with pytest.raises(AIResponseParseError, match="depth"):
        parse_model_json_object(value)


def test_parse_rejects_excessive_json_node_count():
    with pytest.raises(AIResponseParseError, match="maximum"):
        parse_model_json_object({"items": [None] * (MAX_JSON_NODES + 1)})


def test_parse_accepts_unicode_json_without_data_loss():
    result = parse_model_json_object('{"summary":"Mochi 🐾 is safe"}')
    assert result["summary"] == "Mochi 🐾 is safe"


# ---------------------------------------------------------------------------
# Gemini client defensive behavior
# ---------------------------------------------------------------------------


class _StaticModels:
    def __init__(self, *, text=None, error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text)


def _gemini_without_sdk(text=None, error: Exception | None = None):
    client = GeminiAIClient.__new__(GeminiAIClient)
    client._api_key = "unit-test-secret"
    client.model_name = "unit-test-model"
    client.client = SimpleNamespace(models=_StaticModels(text=text, error=error))
    return client


def test_gemini_client_accepts_markdown_fenced_json():
    client = _gemini_without_sdk('```json\n{"status":"ok"}\n```')
    assert client.generate_json("system", {"input": "safe"}) == {"status": "ok"}


def test_gemini_client_preserves_malformed_output_as_parse_error():
    client = _gemini_without_sdk("not json")
    with pytest.raises(AIResponseParseError, match="standalone JSON"):
        client.generate_json("system", {"input": "safe"})


def test_gemini_client_redacts_api_key_from_sdk_exception():
    secret = "unit-test-secret"
    client = _gemini_without_sdk(error=RuntimeError(f"Authorization: {secret}"))
    with pytest.raises(AIConfigError) as exc_info:
        client.generate_json("system", {"input": "safe"})
    assert secret not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_gemini_client_rejects_secret_in_prompt_before_network_call():
    client = _gemini_without_sdk('{"status":"ok"}')
    with pytest.raises(ValueError, match="API key"):
        client.generate_json("system unit-test-secret", {"input": "safe"})
    assert client.client.models.calls == []


def test_gemini_client_rejects_secret_in_payload_before_network_call():
    client = _gemini_without_sdk('{"status":"ok"}')
    with pytest.raises(ValueError, match="API key"):
        client.generate_json("system", {"input": "unit-test-secret"})
    assert client.client.models.calls == []


def test_gemini_client_rejects_nonserializable_payload():
    client = _gemini_without_sdk('{"status":"ok"}')
    with pytest.raises(ValueError, match="JSON-serializable"):
        client.generate_json("system", {"bad": object()})


@pytest.mark.parametrize("prompt", [None, "", "   ", 123])
def test_gemini_client_rejects_invalid_system_prompt(prompt):
    client = _gemini_without_sdk('{"status":"ok"}')
    with pytest.raises((TypeError, ValueError)):
        client.generate_json(prompt, {"input": "safe"})


def test_gemini_client_rejects_non_dictionary_payload():
    client = _gemini_without_sdk('{"status":"ok"}')
    with pytest.raises(TypeError, match="dictionary"):
        client.generate_json("system", ["bad"])


def test_redact_secret_handles_empty_values_safely():
    assert _redact_secret("message", None) == "message"
    assert _redact_secret("", "secret") == ""


# ---------------------------------------------------------------------------
# Critic parsing and validation
# ---------------------------------------------------------------------------


def test_valid_critic_response(snapshot, rules, valid_critic_payload):
    result = PlanCritic(FakeAIClient(valid_critic_payload)).critique(
        snapshot,
        conflicts=[("med-1", "walk-1")],
        retrieved_rules=rules,
    )
    assert result.status is CriticStatus.NEEDS_REVISION
    assert result.issues[0].task_ids == ("med-1", "walk-1")
    assert result.confidence == pytest.approx(0.94)


def test_valid_critic_response_as_plain_json_text(snapshot, rules, valid_critic_payload):
    result = PlanCritic(FakeAIClient(json.dumps(valid_critic_payload))).critique(
        snapshot,
        conflicts=[("med-1", "walk-1")],
        retrieved_rules=rules,
    )
    assert result.status is CriticStatus.NEEDS_REVISION


def test_markdown_fenced_critic_json_is_accepted(snapshot, rules, valid_critic_payload):
    raw = f"```json\n{json.dumps(valid_critic_payload)}\n```"
    result = PlanCritic(FakeAIClient(raw)).critique(
        snapshot,
        conflicts=[("med-1", "walk-1")],
        retrieved_rules=rules,
    )
    assert result.summary.startswith("A fixed medication")


def test_conflict_free_critic_response(snapshot, conflict_free_critic_payload):
    result = PlanCritic(FakeAIClient(conflict_free_critic_payload)).critique(snapshot)
    assert result.status is CriticStatus.NO_CHANGE_NEEDED
    assert result.issues == ()


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"status":"no_change_needed"',
        'Here is the result: {"status":"no_change_needed"}',
        '```python\n{"status":"no_change_needed"}\n```',
    ],
)
def test_critic_malformed_or_prose_output_is_controlled(snapshot, raw):
    with pytest.raises(PlanCriticError, match="Invalid critic output"):
        PlanCritic(FakeAIClient(raw)).critique(snapshot)


def test_critic_missing_required_field_is_rejected(
    snapshot, conflict_free_critic_payload
):
    payload = copy.deepcopy(conflict_free_critic_payload)
    payload.pop("confidence")
    with pytest.raises(PlanCriticError, match="missing required"):
        PlanCritic(FakeAIClient(payload)).critique(snapshot)


def test_critic_unknown_top_level_field_is_rejected(
    snapshot, conflict_free_critic_payload
):
    payload = copy.deepcopy(conflict_free_critic_payload)
    payload["chain_of_thought"] = "hidden"
    with pytest.raises(PlanCriticError, match="unknown field"):
        PlanCritic(FakeAIClient(payload)).critique(snapshot)


@pytest.mark.parametrize("wrong_issues", [None, {}, "issue", True, 1])
def test_critic_wrong_nested_issues_type_is_rejected(
    snapshot, conflict_free_critic_payload, wrong_issues
):
    payload = copy.deepcopy(conflict_free_critic_payload)
    payload["issues"] = wrong_issues
    with pytest.raises(PlanCriticError, match="issues must be a list"):
        PlanCritic(FakeAIClient(payload)).critique(snapshot)


@pytest.mark.parametrize("confidence", [True, False])
def test_critic_boolean_confidence_is_rejected(
    snapshot, conflict_free_critic_payload, confidence
):
    payload = copy.deepcopy(conflict_free_critic_payload)
    payload["confidence"] = confidence
    with pytest.raises(PlanCriticError, match="must be a number"):
        PlanCritic(FakeAIClient(payload)).critique(snapshot)


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("inf"), float("-inf"), float("nan")])
def test_critic_confidence_outside_finite_zero_to_one_is_rejected(
    snapshot, conflict_free_critic_payload, confidence
):
    payload = copy.deepcopy(conflict_free_critic_payload)
    payload["confidence"] = confidence
    with pytest.raises(
        PlanCriticError,
        match="finite number between 0 and 1|non-finite numeric value",
    ):
        PlanCritic(FakeAIClient(payload)).critique(snapshot)


def test_critic_unknown_issue_type_is_rejected(snapshot, rules, valid_critic_payload):
    payload = copy.deepcopy(valid_critic_payload)
    payload["issues"][0]["issue_type"] = "medical_diagnosis"
    with pytest.raises(PlanCriticError, match="issue_type"):
        PlanCritic(FakeAIClient(payload)).critique(
            snapshot, conflicts=[("med-1", "walk-1")], retrieved_rules=rules
        )


def test_critic_unknown_severity_is_rejected(snapshot, rules, valid_critic_payload):
    payload = copy.deepcopy(valid_critic_payload)
    payload["issues"][0]["severity"] = "critical"
    with pytest.raises(PlanCriticError, match="severity"):
        PlanCritic(FakeAIClient(payload)).critique(
            snapshot, conflicts=[("med-1", "walk-1")], retrieved_rules=rules
        )


def test_critic_unknown_task_id_is_rejected_before_repair(
    snapshot, rules, valid_critic_payload
):
    payload = copy.deepcopy(valid_critic_payload)
    payload["issues"][0]["task_ids"] = ["invented-task"]
    with pytest.raises(PlanCriticError, match="unknown task ID"):
        PlanCritic(FakeAIClient(payload)).critique(
            snapshot, conflicts=[("med-1", "walk-1")], retrieved_rules=rules
        )


def test_critic_unknown_rule_section_is_rejected(
    snapshot, rules, valid_critic_payload
):
    payload = copy.deepcopy(valid_critic_payload)
    payload["issues"][0]["rule_sections"] = ["Internet Medical Advice"]
    with pytest.raises(PlanCriticError, match="was not retrieved"):
        PlanCritic(FakeAIClient(payload)).critique(
            snapshot, conflicts=[("med-1", "walk-1")], retrieved_rules=rules
        )


def test_critic_needs_revision_with_empty_issues_is_rejected(snapshot):
    payload = {
        "status": "needs_revision",
        "summary": "Revision needed.",
        "issues": [],
        "confidence": 0.7,
    }
    with pytest.raises(PlanCriticError, match="at least one issue"):
        PlanCritic(FakeAIClient(payload)).critique(snapshot)


def test_critic_no_change_needed_with_issue_is_rejected(
    snapshot, rules, valid_critic_payload
):
    payload = copy.deepcopy(valid_critic_payload)
    payload["status"] = "no_change_needed"
    with pytest.raises(PlanCriticError, match="empty issues"):
        PlanCritic(FakeAIClient(payload)).critique(
            snapshot, conflicts=[("med-1", "walk-1")], retrieved_rules=rules
        )


def test_critic_issue_count_is_capped(snapshot, valid_critic_payload):
    payload = copy.deepcopy(valid_critic_payload)
    issue = payload["issues"][0]
    issue["rule_sections"] = []
    payload["issues"] = [copy.deepcopy(issue) for _ in range(MAX_CRITIC_ISSUES + 1)]
    with pytest.raises(PlanCriticError, match="maximum"):
        PlanCritic(FakeAIClient(payload)).critique(snapshot)


def test_critic_rule_section_count_is_capped(snapshot, rules, valid_critic_payload):
    payload = copy.deepcopy(valid_critic_payload)
    payload["issues"][0]["rule_sections"] = [
        f"Section {index}" for index in range(MAX_RULE_SECTIONS_PER_ISSUE + 1)
    ]
    with pytest.raises(PlanCriticError, match="maximum"):
        PlanCritic(FakeAIClient(payload)).critique(
            snapshot, conflicts=[("med-1", "walk-1")], retrieved_rules=rules
        )


def test_critic_summary_length_is_capped(snapshot, conflict_free_critic_payload):
    payload = copy.deepcopy(conflict_free_critic_payload)
    payload["summary"] = "x" * (MAX_SUMMARY_LENGTH + 1)
    with pytest.raises(PlanCriticError, match="maximum length"):
        PlanCritic(FakeAIClient(payload)).critique(snapshot)


def test_critic_explanation_length_is_capped(snapshot, rules, valid_critic_payload):
    payload = copy.deepcopy(valid_critic_payload)
    payload["issues"][0]["explanation"] = "x" * (MAX_EXPLANATION_LENGTH + 1)
    with pytest.raises(PlanCriticError, match="maximum length"):
        PlanCritic(FakeAIClient(payload)).critique(
            snapshot, conflicts=[("med-1", "walk-1")], retrieved_rules=rules
        )


def test_critic_duplicate_task_id_inside_issue_is_rejected(
    snapshot, rules, valid_critic_payload
):
    payload = copy.deepcopy(valid_critic_payload)
    payload["issues"][0]["task_ids"] = ["walk-1", "walk-1"]
    with pytest.raises(PlanCriticError, match="duplicate value"):
        PlanCritic(FakeAIClient(payload)).critique(
            snapshot, conflicts=[("med-1", "walk-1")], retrieved_rules=rules
        )


def test_critic_rejects_unknown_task_in_deterministic_conflict(snapshot):
    client = FakeAIClient({})
    with pytest.raises(PlanCriticInputError, match="unknown task"):
        PlanCritic(client).critique(snapshot, conflicts=[("walk-1", "ghost")])
    assert client.calls == []


def test_critic_rejects_self_conflict(snapshot):
    with pytest.raises(PlanCriticInputError, match="two different"):
        PlanCritic(FakeAIClient({})).critique(
            snapshot, conflicts=[("walk-1", "walk-1")]
        )


def test_critic_deduplicates_equivalent_conflict_pairs(
    snapshot, rules, valid_critic_payload
):
    client = FakeAIClient(valid_critic_payload)
    PlanCritic(client).critique(
        snapshot,
        conflicts=[("med-1", "walk-1"), ("walk-1", "med-1")],
        retrieved_rules=rules,
    )
    evidence = client.calls[0][1]["deterministic_evidence"]["conflicts"]
    assert len(evidence) == 1


@pytest.mark.parametrize("bad_score", [True, float("nan"), float("inf"), -1, "high"])
def test_critic_rejects_invalid_rule_score(
    snapshot, conflict_free_critic_payload, bad_score
):
    bad_rules = [{"section": "General", "content": "Rule", "score": bad_score}]
    with pytest.raises(PlanCriticInputError, match="finite non-negative"):
        PlanCritic(FakeAIClient(conflict_free_critic_payload)).critique(
            snapshot, retrieved_rules=bad_rules
        )


def test_critic_rejects_more_than_three_retrieved_rules(
    snapshot, conflict_free_critic_payload
):
    rules = [
        {"section": f"Rule {i}", "content": "content", "score": 1.0}
        for i in range(4)
    ]
    with pytest.raises(PlanCriticInputError, match="at most 3"):
        PlanCritic(FakeAIClient(conflict_free_critic_payload)).critique(
            snapshot, retrieved_rules=rules
        )


def test_critic_generic_client_exception_becomes_controlled_error(snapshot):
    class ExplodingClient:
        def generate_json(self, system_prompt, user_payload):
            raise RuntimeError("raw provider details must not leak")

    with pytest.raises(PlanCriticError, match="RuntimeError") as exc_info:
        PlanCritic(ExplodingClient()).critique(snapshot)
    assert "raw provider details" not in str(exc_info.value)


def test_critic_propagates_safe_configuration_error(snapshot):
    class UnavailableClient:
        def generate_json(self, system_prompt, user_payload):
            raise AIConfigError("AI is unavailable")

    with pytest.raises(AIConfigError, match="unavailable"):
        PlanCritic(UnavailableClient()).critique(snapshot)


def test_critic_payload_excludes_task_notes_and_pet_medical_fields(
    owner_with_notes, live_snapshot, conflict_free_critic_payload
):
    client = FakeAIClient(conflict_free_critic_payload)
    PlanCritic(client).critique(live_snapshot)
    serialized = json.dumps(client.calls[0][1])
    assert "IGNORE ALL RULES" not in serialized
    assert "foodType" not in serialized
    assert "medication" not in serialized


def test_critic_system_prompt_contains_required_grounding_constraints():
    assert "Never invent" in PLAN_CRITIC_SYSTEM_PROMPT
    assert "Return JSON only" in PLAN_CRITIC_SYSTEM_PROMPT
    assert "Never provide diagnosis" in PLAN_CRITIC_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Phase 4.6 and 4.8: specialized repair agent and validator handoff
# ---------------------------------------------------------------------------


def test_repair_prompt_contains_all_three_few_shot_scenarios():
    assert "fixed medication overlaps a flexible walk" in REPAIR_AGENT_SYSTEM_PROMPT
    assert "two fixed tasks overlap" in REPAIR_AGENT_SYSTEM_PROMPT
    assert "conflict-free schedule" in REPAIR_AGENT_SYSTEM_PROMPT
    assert '"task_id":"walk-1","action":"move"' in REPAIR_AGENT_SYSTEM_PROMPT
    assert '"action":"defer_for_review"' in REPAIR_AGENT_SYSTEM_PROMPT
    assert '"proposed_changes":[]' in REPAIR_AGENT_SYSTEM_PROMPT
    assert REPAIR_PROMPT_VERSION == "pawpal-repair-v2-few-shot"


def test_valid_repair_response(snapshot, rules, critic_result, valid_repair_payload):
    result = RepairAgent(FakeAIClient(valid_repair_payload)).propose(
        snapshot, critic_result, retrieved_rules=rules
    )
    assert result.proposed_changes[0].task_id == "walk-1"
    assert result.proposed_changes[0].new_time == "09:00"


def test_markdown_fenced_repair_json_is_accepted(
    snapshot, rules, critic_result, valid_repair_payload
):
    raw = f"```json\n{json.dumps(valid_repair_payload)}\n```"
    result = RepairAgent(FakeAIClient(raw)).propose(
        snapshot, critic_result, retrieved_rules=rules
    )
    assert result.summary == "Move the flexible walk to 09:00."


def test_no_change_critic_skips_repair_client_call(
    snapshot, conflict_free_critic_payload
):
    critic = CriticResult.from_dict(
        conflict_free_critic_payload,
        known_task_ids={task.task_id for task in snapshot.tasks},
        known_rule_sections=set(),
    )
    client = FakeAIClient("this must not be used")
    result = RepairAgent(client).propose(snapshot, critic)
    assert result.proposed_changes == ()
    assert client.calls == []


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"proposed_changes":[]',
        'Result: {"proposed_changes":[],"summary":"none"}',
        '```python\n{"proposed_changes":[],"summary":"none"}\n```',
    ],
)
def test_repair_malformed_or_prose_output_is_controlled(
    snapshot, critic_result, raw
):
    with pytest.raises(RepairAgentError, match="Invalid repair output"):
        RepairAgent(FakeAIClient(raw)).propose(snapshot, critic_result)


def test_repair_missing_required_field_is_rejected(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload.pop("summary")
    with pytest.raises(RepairAgentError, match="missing required"):
        RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)


def test_repair_unknown_top_level_field_is_rejected(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["approved"] = True
    with pytest.raises(RepairAgentError, match="unknown field"):
        RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)


@pytest.mark.parametrize("wrong_changes", [None, {}, "move", True, 1])
def test_repair_wrong_proposed_changes_type_is_rejected(
    snapshot, critic_result, valid_repair_payload, wrong_changes
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"] = wrong_changes
    with pytest.raises(RepairAgentError, match="must be a list"):
        RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)


def test_repair_change_count_cannot_exceed_reviewed_tasks(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"] = [
        copy.deepcopy(valid_repair_payload["proposed_changes"][0]) for _ in range(4)
    ]
    with pytest.raises(RepairAgentError, match="maximum is 3"):
        RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)


def test_repair_global_change_count_cap_applies_even_for_large_schedule():
    change = {
        "task_id": "task",
        "action": "keep",
        "original_time": None,
        "new_time": None,
        "reason": "Keep it.",
    }
    payload = {
        "proposed_changes": [copy.deepcopy(change) for _ in range(MAX_REPAIR_CHANGES + 1)],
        "summary": "Too many.",
    }
    with pytest.raises(AIResponseValidationError, match=f"maximum is {MAX_REPAIR_CHANGES}"):
        RepairResult.from_dict(payload, max_changes=1000)


@pytest.mark.parametrize("bad_time", [7, True, [], {}, "", " 09:00", "09:00 "])
def test_repair_rejects_invalid_nested_time_types_or_whitespace(
    snapshot, critic_result, valid_repair_payload, bad_time
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"][0]["new_time"] = bad_time
    with pytest.raises(RepairAgentError, match="new_time"):
        RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)


def test_repair_reason_length_is_capped(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"][0]["reason"] = "x" * (MAX_REASON_LENGTH + 1)
    with pytest.raises(RepairAgentError, match="maximum length"):
        RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)


def test_repair_summary_length_is_capped(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["summary"] = "x" * (MAX_SUMMARY_LENGTH + 1)
    with pytest.raises(RepairAgentError, match="maximum length"):
        RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)


def test_repair_invented_task_is_rejected_before_validator(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"][0]["task_id"] = "invented-task"
    with pytest.raises(RepairAgentError, match="outside the critic issues"):
        RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)


def test_repair_existing_but_out_of_issue_scope_task_is_rejected(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"][0]["task_id"] = "feed-1"
    with pytest.raises(RepairAgentError, match="outside the critic issues"):
        RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)


def test_repair_delete_action_reaches_validator_and_is_rejected(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"][0]["action"] = "delete"
    payload["proposed_changes"][0]["new_time"] = None
    result = RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)
    validation = ScheduleValidator().validate(
        snapshot, result.to_dict()["proposed_changes"]
    )
    assert validation.valid is False
    assert validation.checks["actions_allowed"] is False


def test_repair_medication_move_reaches_validator_and_is_rejected(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"][0].update(
        {
            "task_id": "med-1",
            "action": "move",
            "original_time": "08:00",
            "new_time": "09:00",
        }
    )
    result = RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)
    validation = ScheduleValidator().validate(
        snapshot, result.to_dict()["proposed_changes"]
    )
    assert validation.valid is False
    assert validation.checks["fixed_tasks_unchanged"] is False
    assert validation.checks["medication_tasks_unchanged"] is False


def test_repair_move_outside_availability_is_rejected_by_validator(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"][0]["new_time"] = "19:00"
    result = RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)
    validation = ScheduleValidator().validate(
        snapshot, result.to_dict()["proposed_changes"]
    )
    assert validation.valid is False
    assert validation.checks["inside_availability"] is False


def test_repair_malformed_time_is_rejected_by_validator(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"][0]["new_time"] = "9:00"
    result = RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)
    validation = ScheduleValidator().validate(
        snapshot, result.to_dict()["proposed_changes"]
    )
    assert validation.valid is False
    assert validation.checks["times_valid"] is False


def test_repair_new_conflict_is_rejected_by_validator(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"][0]["new_time"] = "12:00"
    result = RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)
    validation = ScheduleValidator().validate(
        snapshot, result.to_dict()["proposed_changes"]
    )
    assert validation.valid is False
    assert validation.checks["no_new_conflicts"] is False


def test_repair_extra_protected_field_is_rejected_during_parsing(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    payload["proposed_changes"][0]["new_duration"] = 5
    with pytest.raises(RepairAgentError, match="unknown field"):
        RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)


def test_repair_duplicate_task_changes_are_rejected_by_validator(
    snapshot, critic_result, valid_repair_payload
):
    payload = copy.deepcopy(valid_repair_payload)
    second = copy.deepcopy(payload["proposed_changes"][0])
    second["new_time"] = "10:00"
    payload["proposed_changes"].append(second)
    result = RepairAgent(FakeAIClient(payload)).propose(snapshot, critic_result)
    validation = ScheduleValidator().validate(
        snapshot, result.to_dict()["proposed_changes"]
    )
    assert validation.valid is False
    assert validation.checks["schema_valid"] is False
    assert any("duplicate proposal" in error for error in validation.errors)


def test_valid_repair_passes_validator_without_mutating_snapshot(
    snapshot, critic_result, valid_repair_payload
):
    before = copy.deepcopy(snapshot)
    result = RepairAgent(FakeAIClient(valid_repair_payload)).propose(
        snapshot, critic_result
    )
    validation = ScheduleValidator().validate(
        snapshot, result.to_dict()["proposed_changes"]
    )
    assert validation.valid is True
    assert snapshot == before


def test_repair_proposal_does_not_mutate_live_owner(
    owner_with_notes, valid_repair_payload
):
    snapshot = build_schedule_snapshot(owner_with_notes)
    critic_payload = {
        "status": "needs_revision",
        "summary": "The walk needs review.",
        "issues": [
            {
                "issue_type": "schedule_conflict",
                "task_ids": ["walk-1"],
                "severity": "medium",
                "explanation": "A scheduling issue exists.",
                "rule_sections": [],
            }
        ],
        "confidence": 0.8,
    }
    critic = CriticResult.from_dict(
        critic_payload,
        known_task_ids={"walk-1"},
        known_rule_sections=set(),
    )
    original_time = owner_with_notes.scheduler.tasks[0].preferredTime
    RepairAgent(FakeAIClient(valid_repair_payload)).propose(snapshot, critic)
    assert owner_with_notes.scheduler.tasks[0].preferredTime == original_time


@pytest.mark.parametrize("bad_score", [True, float("nan"), float("inf"), -1, "high"])
def test_repair_rejects_invalid_rule_score(
    snapshot, critic_result, valid_repair_payload, bad_score
):
    rules = [{"section": "General", "content": "Rule", "score": bad_score}]
    with pytest.raises(RepairAgentInputError, match="finite non-negative"):
        RepairAgent(FakeAIClient(valid_repair_payload)).propose(
            snapshot, critic_result, retrieved_rules=rules
        )


def test_repair_rejects_more_than_three_retrieved_rules(
    snapshot, critic_result, valid_repair_payload
):
    rules = [
        {"section": f"Rule {i}", "content": "content", "score": 1.0}
        for i in range(4)
    ]
    with pytest.raises(RepairAgentInputError, match="at most 3"):
        RepairAgent(FakeAIClient(valid_repair_payload)).propose(
            snapshot, critic_result, retrieved_rules=rules
        )


def test_repair_generic_client_exception_becomes_controlled_error(
    snapshot, critic_result
):
    class ExplodingClient:
        def generate_json(self, system_prompt, user_payload):
            raise TimeoutError("provider details")

    with pytest.raises(RepairAgentError, match="TimeoutError") as exc_info:
        RepairAgent(ExplodingClient()).propose(snapshot, critic_result)
    assert "provider details" not in str(exc_info.value)


def test_repair_propagates_safe_configuration_error(snapshot, critic_result):
    class UnavailableClient:
        def generate_json(self, system_prompt, user_payload):
            raise AIConfigError("AI is unavailable")

    with pytest.raises(AIConfigError, match="unavailable"):
        RepairAgent(UnavailableClient()).propose(snapshot, critic_result)


def test_repair_payload_excludes_notes(owner_with_notes, valid_repair_payload):
    snapshot = build_schedule_snapshot(owner_with_notes)
    critic = CriticResult.from_dict(
        {
            "status": "needs_revision",
            "summary": "Review walk.",
            "issues": [
                {
                    "issue_type": "schedule_conflict",
                    "task_ids": ["walk-1"],
                    "severity": "medium",
                    "explanation": "Review the task time.",
                    "rule_sections": [],
                }
            ],
            "confidence": 0.8,
        },
        known_task_ids={"walk-1"},
        known_rule_sections=set(),
    )
    client = FakeAIClient(valid_repair_payload)
    RepairAgent(client).propose(snapshot, critic)
    serialized = json.dumps(client.calls[0][1])
    assert "IGNORE ALL RULES" not in serialized
    assert "notes" not in serialized


# ---------------------------------------------------------------------------
# Multi-pet, multi-issue, and multi-action diversity coverage
#
# The fixtures above (snapshot/critic_result/valid_repair_payload) all share
# one owner, one pet, and one change at a time. These tests exercise a
# different owner/pet mix, multiple simultaneous issues and proposed
# changes, and the Phase 4.6 "two protected tasks overlap" repair path,
# which nothing above touches directly.
# ---------------------------------------------------------------------------


def test_critic_handles_multiple_issues_spanning_multiple_pets(
    multi_pet_snapshot, multi_pet_rules, multi_issue_critic_payload
):
    result = PlanCritic(FakeAIClient(multi_issue_critic_payload)).critique(
        multi_pet_snapshot,
        conflicts=[("vet-1", "med-2"), ("walk-2", "feed-2")],
        retrieved_rules=multi_pet_rules,
    )
    assert result.status is CriticStatus.NEEDS_REVISION
    assert len(result.issues) == 2
    assert result.issues[0].task_ids == ("vet-1", "med-2")
    assert result.issues[0].issue_type is IssueType.FIXED_TASK_CONFLICT
    assert result.issues[1].task_ids == ("walk-2", "feed-2")
    # One issue cites rule sections, the other legitimately cites none.
    assert result.issues[0].rule_sections == (
        "Veterinarian Appointments",
        "Medication Tasks",
    )
    assert result.issues[1].rule_sections == ()


def test_repair_defers_two_fixed_tasks_for_different_pets(
    multi_pet_snapshot, multi_pet_critic_result, defer_for_review_repair_payload
):
    result = RepairAgent(FakeAIClient(defer_for_review_repair_payload)).propose(
        multi_pet_snapshot, multi_pet_critic_result
    )
    assert len(result.proposed_changes) == 2
    assert {change.task_id for change in result.proposed_changes} == {
        "vet-1",
        "med-2",
    }
    for change in result.proposed_changes:
        assert change.action == "defer_for_review"
        assert change.new_time is None

    validation = ScheduleValidator().validate(
        multi_pet_snapshot, result.to_dict()["proposed_changes"]
    )
    assert validation.valid is True
    assert validation.checks["fixed_tasks_unchanged"] is True
    assert validation.checks["medication_tasks_unchanged"] is True


def test_repair_proposes_multiple_changes_across_different_pets_at_once(
    multi_pet_snapshot, multi_pet_critic_result, multi_change_repair_payload
):
    result = RepairAgent(FakeAIClient(multi_change_repair_payload)).propose(
        multi_pet_snapshot, multi_pet_critic_result
    )
    actions_by_task = {
        change.task_id: change.action for change in result.proposed_changes
    }
    assert actions_by_task == {
        "vet-1": "defer_for_review",
        "med-2": "defer_for_review",
        "walk-2": "move",
        "feed-2": "keep",
    }

    validation = ScheduleValidator().validate(
        multi_pet_snapshot, result.to_dict()["proposed_changes"]
    )
    assert validation.valid is True


def test_repair_response_mixing_move_keep_and_defer_actions_parses_correctly(
    multi_change_repair_payload,
):
    # Schema-level check, independent of critic scoping: one response can
    # legally mix all three allowed actions in a single proposal.
    result = RepairResult.from_dict(multi_change_repair_payload, max_changes=10)
    assert [change.action for change in result.proposed_changes] == [
        "defer_for_review",
        "defer_for_review",
        "move",
        "keep",
    ]


def test_critic_issue_accepts_task_ids_at_the_maximum_boundary(
    conflict_free_critic_payload,
):
    payload = copy.deepcopy(conflict_free_critic_payload)
    payload["status"] = "needs_revision"
    payload["issues"] = [
        {
            "issue_type": "capacity_limit",
            "task_ids": [f"task-{index}" for index in range(MAX_TASK_IDS_PER_ISSUE)],
            "severity": "low",
            "explanation": "Too many tasks are packed into one window.",
            "rule_sections": [],
        }
    ]
    result = CriticResult.from_dict(payload)
    assert len(result.issues[0].task_ids) == MAX_TASK_IDS_PER_ISSUE


def test_critic_issue_accepts_rule_sections_at_the_maximum_boundary(
    conflict_free_critic_payload,
):
    payload = copy.deepcopy(conflict_free_critic_payload)
    payload["status"] = "needs_revision"
    payload["issues"] = [
        {
            "issue_type": "care_rule_violation",
            "task_ids": ["task-1"],
            "severity": "medium",
            "explanation": "Multiple rule sections apply to this conflict.",
            "rule_sections": [
                f"Section {index}" for index in range(MAX_RULE_SECTIONS_PER_ISSUE)
            ],
        }
    ]
    result = CriticResult.from_dict(payload)
    assert len(result.issues[0].rule_sections) == MAX_RULE_SECTIONS_PER_ISSUE


def test_critic_allows_mixing_issues_with_and_without_rule_sections(
    conflict_free_critic_payload,
):
    payload = copy.deepcopy(conflict_free_critic_payload)
    payload["status"] = "needs_revision"
    payload["issues"] = [
        {
            "issue_type": "unscheduled_task",
            "task_ids": ["task-1"],
            "severity": "low",
            "explanation": "This task could not be scheduled.",
            "rule_sections": [],
        },
        {
            "issue_type": "care_rule_violation",
            "task_ids": ["task-2"],
            "severity": "high",
            "explanation": "A care rule was violated.",
            "rule_sections": ["General Scheduling"],
        },
    ]
    result = CriticResult.from_dict(payload)
    assert result.issues[0].rule_sections == ()
    assert result.issues[1].rule_sections == ("General Scheduling",)


# ---------------------------------------------------------------------------
# Missing configuration and PawPal+ regression safety
# ---------------------------------------------------------------------------


def test_missing_api_key_is_controlled_and_base_pawpal_still_runs(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(AIConfigError, match="Missing GEMINI_API_KEY"):
        GeminiAIClient()

    owner = Owner(
        name="Jordan",
        startTime=time(7, 0),
        endTime=time(19, 0),
        preferences={},
    )
    pet = Pet(
        name="Mochi",
        species="Dog",
        breed="Mix",
        age=2,
        foodType="Kibble",
        medication="none",
        energyLevel=5,
    )
    owner.addPet(pet)
    owner.scheduler.addTask(
        Task(
            taskId="walk-base",
            taskName="Walk",
            taskType="walk",
            durationMinutes=20,
            priority=Priority.MEDIUM,
            pet=pet,
            preferredTime=time(8, 0),
            dueDate=date.today(),
        )
    )
    assert [task.taskId for task in owner.scheduler.generatePlan()] == ["walk-base"]


def test_ai_components_reject_invalid_client_at_construction():
    with pytest.raises(TypeError, match="generate_json"):
        PlanCritic(None)
    with pytest.raises(TypeError, match="generate_json"):
        RepairAgent(object())


def test_snapshot_type_is_required_for_both_components(
    conflict_free_critic_payload, critic_result, valid_repair_payload
):
    with pytest.raises(PlanCriticInputError, match="ScheduleSnapshot"):
        PlanCritic(FakeAIClient(conflict_free_critic_payload)).critique({})
    with pytest.raises(RepairAgentInputError, match="ScheduleSnapshot"):
        RepairAgent(FakeAIClient(valid_repair_payload)).propose({}, critic_result)