"""Phase 5.1 and 5.2 orchestration for PawPal Sentinel.

This service coordinates the existing deterministic scheduler, immutable
snapshot builder, care-rule retriever, AI critic, AI repair agent, and
ScheduleValidator. It never mutates a live task. A valid result stops at
AWAITING_OWNER_APPROVAL; Phase 5.5 approval is the only place that may call
apply_approved_changes().
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Callable

from agent_logger import AgentLogError, AgentLogger
from ai_client import AIClient, AIConfigError
from pawpal_system import Owner
from plan_critic import (
    CRITIC_PROMPT_VERSION,
    PlanCritic,
    PlanCriticError,
    PlanCriticInputError,
)
from repair_agent import (
    REPAIR_PROMPT_VERSION,
    RepairAgent,
    RepairAgentError,
    RepairAgentInputError,
)
from retriever import (
    DEFAULT_RULES_PATH,
    MAX_TOP_K,
    RetrievedRule,
    build_retrieval_query,
    retrieve_rules,
)
from schedule_validator import Conflict, ScheduleValidator, find_conflicts
from sentinel_models import (
    CriticResult,
    CriticStatus,
    ProposedChange,
    RepairResult,
    ScheduleSnapshot,
    TaskSnapshot,
    ValidationResult,
    build_schedule_snapshot,
)


class WorkflowStatus(str, Enum):
    NO_REPAIR_NEEDED = "no_repair_needed"
    AWAITING_OWNER_APPROVAL = "awaiting_owner_approval"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    AI_UNAVAILABLE = "ai_unavailable"
    INVALID_AI_OUTPUT = "invalid_ai_output"
    FAILED = "failed"


@dataclass(frozen=True)
class RepairAttempt:
    """One observable repair attempt and its deterministic validation result."""

    attempt: int
    repair_result: RepairResult
    validation_result: ValidationResult


@dataclass(frozen=True)
class AgentRun:
    """Immutable workflow result suitable for UI display and later logging."""

    status: WorkflowStatus
    message: str
    snapshot: ScheduleSnapshot | None = None
    retrieval_query: str = ""
    conflicts: tuple[Conflict, ...] = ()
    unscheduled_task_ids: tuple[str, ...] = ()
    retrieved_rules: tuple[RetrievedRule, ...] = ()
    critic_result: CriticResult | None = None
    repair_attempts: tuple[RepairAttempt, ...] = ()
    validated_changes: tuple[ProposedChange, ...] = ()
    final_validation: ValidationResult | None = None
    warnings: tuple[str, ...] = ()

    @property
    def can_approve(self) -> bool:
        """Only a current, valid proposal may advance to Phase 5.5 approval."""
        return (
            self.status is WorkflowStatus.AWAITING_OWNER_APPROVAL
            and self.snapshot is not None
            and self.final_validation is not None
            and self.final_validation.valid
            and bool(self.validated_changes)
        )


RuleRetriever = Callable[[object, str, int], list[RetrievedRule]]


class PawPalSentinel:
    """Coordinates the bounded multi-step Sentinel review workflow."""

    MAX_REPAIR_ATTEMPTS = 2

    def __init__(
        self,
        ai_client: AIClient,
        *,
        validator: ScheduleValidator | None = None,
        rules_path: str = DEFAULT_RULES_PATH,
        rule_retriever: RuleRetriever = retrieve_rules,
        logger: AgentLogger | None = None,
        enable_logging: bool = True,
    ) -> None:
        if ai_client is None or not callable(getattr(ai_client, "generate_json", None)):
            raise TypeError(
                "ai_client must provide generate_json(system_prompt, user_payload)."
            )
        if validator is not None and not callable(getattr(validator, "validate", None)):
            raise TypeError("validator must provide validate(snapshot, proposed_changes).")
        if not isinstance(rules_path, str) or not rules_path.strip():
            raise ValueError("rules_path must be a non-empty string.")
        if not callable(rule_retriever):
            raise TypeError("rule_retriever must be callable.")
        if not isinstance(enable_logging, bool):
            raise TypeError("enable_logging must be a boolean.")
        if logger is not None and not callable(getattr(logger, "log_run", None)):
            raise TypeError("logger must provide log_run(run, prompt_version=...).")

        self.critic = PlanCritic(ai_client)
        self.repair_agent = RepairAgent(ai_client)
        self.validator = validator or ScheduleValidator()
        self.rules_path = rules_path
        self.rule_retriever = rule_retriever
        self.logger = (logger if logger is not None else AgentLogger()) if enable_logging else None
        self.prompt_version = (
            f"critic:{CRITIC_PROMPT_VERSION}|repair:{REPAIR_PROMPT_VERSION}"
        )

    @staticmethod
    def _proposal_payload(result: RepairResult) -> list[dict[str, object]]:
        """Convert a typed RepairResult into the validator's raw-list boundary."""
        return [
            {
                "task_id": change.task_id,
                "action": change.action,
                "original_time": change.original_time,
                "new_time": change.new_time,
                "reason": change.reason,
            }
            for change in result.proposed_changes
        ]

    @staticmethod
    def _reviewed_tasks(
        owner: Owner,
        snapshot: ScheduleSnapshot,
    ) -> tuple[TaskSnapshot, ...]:
        """Return only today's generated/unscheduled tasks for evidence checks.

        ScheduleSnapshot intentionally contains all pending tasks for versioning,
        including future recurring occurrences. Conflict evidence for this run
        should describe the draft generated just now, not unrelated future work.
        """
        reviewed_ids = {
            task.taskId
            for task in (*owner.scheduler.dailyPlan, *owner.scheduler.unscheduledTasks)
        }
        return tuple(task for task in snapshot.tasks if task.task_id in reviewed_ids)

    @staticmethod
    def _availability_and_capacity_evidence(
        snapshot: ScheduleSnapshot,
        reviewed_tasks: tuple[TaskSnapshot, ...],
    ) -> tuple[frozenset[str], bool]:
        """Return (outside-window task IDs, over-capacity) evidence."""
        try:
            start = datetime.strptime(snapshot.availability_start, "%H:%M")
            end = datetime.strptime(snapshot.availability_end, "%H:%M")
        except (TypeError, ValueError):
            return frozenset(), False

        # Sentinel's validator deliberately rejects overnight windows. Do not
        # fabricate per-task availability evidence for a window this phase does
        # not yet model.
        if end <= start:
            return frozenset(), False

        start_minutes = start.hour * 60 + start.minute
        end_minutes = end.hour * 60 + end.minute
        outside_task_ids: set[str] = set()
        for task in reviewed_tasks:
            task_start = (
                int(task.preferred_time[:2]) * 60
                + int(task.preferred_time[3:])
            )
            if (
                task_start < start_minutes
                or task_start + task.duration_minutes > end_minutes
            ):
                outside_task_ids.add(task.task_id)

        capacity = sum(task.duration_minutes for task in reviewed_tasks) > (
            end_minutes - start_minutes
        )
        return frozenset(outside_task_ids), capacity

    @staticmethod
    def _unsupported_critic_issues(
        critic_result: CriticResult,
        *,
        conflicts: tuple[Conflict, ...],
        unscheduled_task_ids: tuple[str, ...],
        outside_window_task_ids: frozenset[str],
        capacity_limit: bool,
        reviewed_task_ids: frozenset[str],
    ) -> tuple[int, ...]:
        """Return indices of critic issues not grounded in supplied evidence."""
        conflict_pairs = {
            frozenset((item.task_id_a, item.task_id_b)) for item in conflicts
        }
        unscheduled = set(unscheduled_task_ids)
        unsupported: list[int] = []

        for index, issue in enumerate(critic_result.issues):
            issue_ids = set(issue.task_ids)
            issue_type = issue.issue_type.value

            if issue_type in {"schedule_conflict", "fixed_task_conflict"}:
                supported = any(pair.issubset(issue_ids) for pair in conflict_pairs)
            elif issue_type == "availability_violation":
                supported = bool(issue_ids & outside_window_task_ids)
            elif issue_type == "unscheduled_task":
                supported = bool(issue_ids & unscheduled)
            elif issue_type == "capacity_limit":
                supported = capacity_limit and bool(issue_ids & reviewed_task_ids)
            elif issue_type == "care_rule_violation":
                # Rule-based findings must cite at least one retrieved section;
                # CriticResult already verifies that every cited name was
                # actually retrieved and every task ID is known.
                supported = bool(issue.rule_sections)
            else:
                supported = False

            if not supported:
                unsupported.append(index)

        return tuple(unsupported)

    @staticmethod
    def _candidate_tasks(
        snapshot: ScheduleSnapshot,
        changes: tuple[ProposedChange, ...],
    ) -> tuple[TaskSnapshot, ...]:
        moved_times = {
            change.task_id: change.new_time
            for change in changes
            if change.action == "move" and change.new_time is not None
        }
        return tuple(
            replace(task, preferred_time=moved_times[task.task_id])
            if task.task_id in moved_times
            else task
            for task in snapshot.tasks
        )

    @classmethod
    def _require_reviewed_conflicts_resolved(
        cls,
        snapshot: ScheduleSnapshot,
        reviewed_conflicts: tuple[Conflict, ...],
        result: ValidationResult,
    ) -> ValidationResult:
        """Reject a syntactically safe move that leaves the reviewed conflict.

        ScheduleValidator correctly permits defer_for_review for fixed conflicts
        and rejects newly created conflicts. At the orchestration layer, a move
        presented for approval must also resolve the deterministic conflict that
        caused this review; otherwise the workflow would approve a harmless but
        ineffective no-op.
        """
        if not result.valid or not reviewed_conflicts:
            return result
        if any(change.action == "defer_for_review" for change in result.normalized_changes):
            return result

        candidate_conflicts = {
            frozenset((item.task_id_a, item.task_id_b))
            for item in find_conflicts(
                cls._candidate_tasks(snapshot, tuple(result.normalized_changes))
            )
        }
        unresolved = [
            conflict
            for conflict in reviewed_conflicts
            if frozenset((conflict.task_id_a, conflict.task_id_b))
            in candidate_conflicts
        ]
        if not unresolved:
            checks = dict(result.checks)
            checks["reviewed_conflicts_resolved"] = True
            return ValidationResult(
                valid=True,
                errors=list(result.errors),
                checks=checks,
                normalized_changes=list(result.normalized_changes),
            )

        errors = list(result.errors)
        errors.extend(
            "Proposal does not resolve the reviewed conflict between tasks "
            f"'{conflict.task_id_a}' and '{conflict.task_id_b}'."
            for conflict in unresolved
        )
        checks = dict(result.checks)
        checks["reviewed_conflicts_resolved"] = False
        return ValidationResult(
            valid=False,
            errors=errors,
            checks=checks,
            normalized_changes=[],
        )

    @staticmethod
    def _requires_human_review(result: ValidationResult) -> bool:
        """A valid defer/no-move proposal is safe but cannot be auto-approved."""
        actions = [change.action for change in result.normalized_changes]
        return (
            not any(action == "move" for action in actions)
            or any(action == "defer_for_review" for action in actions)
        )

    def _finalize_run(self, run: AgentRun) -> AgentRun:
        """Write one Phase 5.4 record without allowing logging to break the run."""
        if self.logger is None:
            return run
        try:
            self.logger.log_run(run, prompt_version=self.prompt_version)
            return run
        except AgentLogError as exc:
            warning = f"Structured agent logging failed safely: {exc}"
        except Exception as exc:
            # A custom/injected logger must not be able to crash Streamlit.
            warning = (
                "Structured agent logging failed safely "
                f"({type(exc).__name__})."
            )
        return replace(run, warnings=tuple((*run.warnings, warning)))

    def review_plan(self, owner: Owner) -> AgentRun:
        """Execute, log exactly once, and stop before any live mutation."""
        return self._finalize_run(self._review_plan_unlogged(owner))

    def _review_plan_unlogged(self, owner: Owner) -> AgentRun:
        """Execute Phase 5.2/5.3 with at most one repair revision."""
        if not isinstance(owner, Owner):
            return AgentRun(
                status=WorkflowStatus.FAILED,
                message="Sentinel review requires a valid Owner instance.",
            )

        warnings: list[str] = []

        # 1-3: Generate the existing deterministic draft, snapshot it, and
        # collect deterministic conflict/unscheduled evidence.
        try:
            owner.scheduler.generatePlan()
            snapshot = build_schedule_snapshot(owner)
            reviewed_tasks = self._reviewed_tasks(owner, snapshot)
            conflicts = tuple(find_conflicts(reviewed_tasks))
            unscheduled_ids = tuple(snapshot.unscheduled_task_ids)
            outside_window_task_ids, capacity_limit = (
                self._availability_and_capacity_evidence(snapshot, reviewed_tasks)
            )
            availability_violation = bool(outside_window_task_ids)
        except Exception as exc:
            return AgentRun(
                status=WorkflowStatus.FAILED,
                message=(
                    "Sentinel could not build the deterministic draft "
                    f"({type(exc).__name__})."
                ),
            )

        issue_labels: list[str] = []
        if conflicts:
            issue_labels.append("schedule_conflict")
        if unscheduled_ids:
            issue_labels.append("unscheduled_task")
        if availability_violation:
            issue_labels.append("availability_violation")
        if capacity_limit:
            issue_labels.append("capacity_limit")

        # 4-5: Build the query only from structured evidence and retrieve up to
        # three project-controlled rule sections. Retrieval failure degrades to
        # empty evidence rather than crashing normal PawPal+.
        retrieval_query = build_retrieval_query(
            snapshot,
            conflicts=conflicts,
            unscheduled_task_ids=unscheduled_ids,
            availability_violation=availability_violation,
            issue_labels=issue_labels,
        )
        try:
            retrieved_rules = tuple(
                self.rule_retriever(retrieval_query, self.rules_path, MAX_TOP_K)
            ) if retrieval_query else ()
        except Exception as exc:
            retrieved_rules = ()
            warnings.append(
                "Care-rule retrieval was unavailable; the review continued with "
                f"deterministic evidence only ({type(exc).__name__})."
            )

        # 6-7: Critic identifies issues only. Typed parsing errors never reach
        # repair or mutation logic.
        try:
            critic_result = self.critic.critique(
                snapshot,
                conflicts=conflicts,
                unscheduled_task_ids=unscheduled_ids,
                retrieved_rules=retrieved_rules,
            )
        except AIConfigError as exc:
            return AgentRun(
                status=WorkflowStatus.AI_UNAVAILABLE,
                message=str(exc),
                snapshot=snapshot,
                retrieval_query=retrieval_query,
                conflicts=conflicts,
                unscheduled_task_ids=unscheduled_ids,
                retrieved_rules=retrieved_rules,
                warnings=tuple(warnings),
            )
        except PlanCriticInputError as exc:
            return AgentRun(
                status=WorkflowStatus.FAILED,
                message=f"Deterministic critic input was inconsistent: {exc}",
                snapshot=snapshot,
                retrieval_query=retrieval_query,
                conflicts=conflicts,
                unscheduled_task_ids=unscheduled_ids,
                retrieved_rules=retrieved_rules,
                warnings=tuple(warnings),
            )
        except PlanCriticError as exc:
            return AgentRun(
                status=WorkflowStatus.INVALID_AI_OUTPUT,
                message=str(exc),
                snapshot=snapshot,
                retrieval_query=retrieval_query,
                conflicts=conflicts,
                unscheduled_task_ids=unscheduled_ids,
                retrieved_rules=retrieved_rules,
                warnings=tuple(warnings),
            )
        except Exception as exc:
            return AgentRun(
                status=WorkflowStatus.FAILED,
                message=f"Plan critic failed safely ({type(exc).__name__}).",
                snapshot=snapshot,
                retrieval_query=retrieval_query,
                conflicts=conflicts,
                unscheduled_task_ids=unscheduled_ids,
                retrieved_rules=retrieved_rules,
                warnings=tuple(warnings),
            )

        deterministic_issue_exists = bool(conflicts or unscheduled_ids)
        if critic_result.status is CriticStatus.NO_CHANGE_NEEDED:
            if deterministic_issue_exists:
                return AgentRun(
                    status=WorkflowStatus.INVALID_AI_OUTPUT,
                    message=(
                        "The critic reported no change needed, but deterministic "
                        "conflict or unscheduled-task evidence exists."
                    ),
                    snapshot=snapshot,
                    retrieval_query=retrieval_query,
                    conflicts=conflicts,
                    unscheduled_task_ids=unscheduled_ids,
                    retrieved_rules=retrieved_rules,
                    critic_result=critic_result,
                    warnings=tuple(warnings),
                )
            return AgentRun(
                status=WorkflowStatus.NO_REPAIR_NEEDED,
                message=critic_result.summary,
                snapshot=snapshot,
                retrieval_query=retrieval_query,
                conflicts=conflicts,
                unscheduled_task_ids=unscheduled_ids,
                retrieved_rules=retrieved_rules,
                critic_result=critic_result,
                warnings=tuple(warnings),
            )

        unsupported_issue_indices = self._unsupported_critic_issues(
            critic_result,
            conflicts=conflicts,
            unscheduled_task_ids=unscheduled_ids,
            outside_window_task_ids=outside_window_task_ids,
            capacity_limit=capacity_limit,
            reviewed_task_ids=frozenset(task.task_id for task in reviewed_tasks),
        )
        if unsupported_issue_indices:
            if len(unsupported_issue_indices) == len(critic_result.issues):
                warnings.append(
                    "The critic reported only issues unsupported by deterministic "
                    "evidence, so no repair request was sent."
                )
                return AgentRun(
                    status=WorkflowStatus.NO_REPAIR_NEEDED,
                    message="No deterministically supported issue requires repair.",
                    snapshot=snapshot,
                    retrieval_query=retrieval_query,
                    conflicts=conflicts,
                    unscheduled_task_ids=unscheduled_ids,
                    retrieved_rules=retrieved_rules,
                    critic_result=critic_result,
                    warnings=tuple(warnings),
                )
            return AgentRun(
                status=WorkflowStatus.INVALID_AI_OUTPUT,
                message=(
                    "The critic mixed grounded findings with unsupported issue "
                    "claims; repair was stopped safely."
                ),
                snapshot=snapshot,
                retrieval_query=retrieval_query,
                conflicts=conflicts,
                unscheduled_task_ids=unscheduled_ids,
                retrieved_rules=retrieved_rules,
                critic_result=critic_result,
                warnings=tuple(warnings),
            )

        # 8-13: One initial proposal plus at most one validator-guided revision.
        attempts: list[RepairAttempt] = []
        previous_result: RepairResult | None = None
        previous_validation: ValidationResult | None = None

        for attempt_number in range(1, self.MAX_REPAIR_ATTEMPTS + 1):
            try:
                if attempt_number == 1:
                    repair_result = self.repair_agent.propose(
                        snapshot,
                        critic_result,
                        retrieved_rules=retrieved_rules,
                    )
                else:
                    # This branch is reachable only after a validator-invalid
                    # first result, so both values are guaranteed to exist.
                    repair_result = self.repair_agent.revise(
                        snapshot,
                        critic_result,
                        previous_result,
                        previous_validation.errors,
                        retrieved_rules=retrieved_rules,
                    )
            except AIConfigError as exc:
                return AgentRun(
                    status=WorkflowStatus.AI_UNAVAILABLE,
                    message=str(exc),
                    snapshot=snapshot,
                    retrieval_query=retrieval_query,
                    conflicts=conflicts,
                    unscheduled_task_ids=unscheduled_ids,
                    retrieved_rules=retrieved_rules,
                    critic_result=critic_result,
                    repair_attempts=tuple(attempts),
                    warnings=tuple(warnings),
                )
            except RepairAgentInputError as exc:
                return AgentRun(
                    status=WorkflowStatus.FAILED,
                    message=f"Deterministic repair input was inconsistent: {exc}",
                    snapshot=snapshot,
                    retrieval_query=retrieval_query,
                    conflicts=conflicts,
                    unscheduled_task_ids=unscheduled_ids,
                    retrieved_rules=retrieved_rules,
                    critic_result=critic_result,
                    repair_attempts=tuple(attempts),
                    warnings=tuple(warnings),
                )
            except RepairAgentError as exc:
                return AgentRun(
                    status=WorkflowStatus.INVALID_AI_OUTPUT,
                    message=str(exc),
                    snapshot=snapshot,
                    retrieval_query=retrieval_query,
                    conflicts=conflicts,
                    unscheduled_task_ids=unscheduled_ids,
                    retrieved_rules=retrieved_rules,
                    critic_result=critic_result,
                    repair_attempts=tuple(attempts),
                    warnings=tuple(warnings),
                )
            except Exception as exc:
                return AgentRun(
                    status=WorkflowStatus.FAILED,
                    message=f"Repair agent failed safely ({type(exc).__name__}).",
                    snapshot=snapshot,
                    retrieval_query=retrieval_query,
                    conflicts=conflicts,
                    unscheduled_task_ids=unscheduled_ids,
                    retrieved_rules=retrieved_rules,
                    critic_result=critic_result,
                    repair_attempts=tuple(attempts),
                    warnings=tuple(warnings),
                )

            validation = self.validator.validate(
                snapshot,
                self._proposal_payload(repair_result),
            )
            validation = self._require_reviewed_conflicts_resolved(
                snapshot,
                conflicts,
                validation,
            )
            attempts.append(
                RepairAttempt(
                    attempt=attempt_number,
                    repair_result=repair_result,
                    validation_result=validation,
                )
            )

            if validation.valid:
                if self._requires_human_review(validation):
                    return AgentRun(
                        status=WorkflowStatus.HUMAN_REVIEW_REQUIRED,
                        message=(
                            "The proposal is structurally safe, but it contains "
                            "no complete movable repair and requires owner review."
                        ),
                        snapshot=snapshot,
                        retrieval_query=retrieval_query,
                        conflicts=conflicts,
                        unscheduled_task_ids=unscheduled_ids,
                        retrieved_rules=retrieved_rules,
                        critic_result=critic_result,
                        repair_attempts=tuple(attempts),
                        final_validation=validation,
                        warnings=tuple(warnings),
                    )

                return AgentRun(
                    status=WorkflowStatus.AWAITING_OWNER_APPROVAL,
                    message=(
                        "A validated repair is ready for owner approval. "
                        "No live task has been changed."
                    ),
                    snapshot=snapshot,
                    retrieval_query=retrieval_query,
                    conflicts=conflicts,
                    unscheduled_task_ids=unscheduled_ids,
                    retrieved_rules=retrieved_rules,
                    critic_result=critic_result,
                    repair_attempts=tuple(attempts),
                    validated_changes=tuple(validation.normalized_changes),
                    final_validation=validation,
                    warnings=tuple(warnings),
                )

            previous_result = repair_result
            previous_validation = validation

        # Exactly two attempts were used; never request a third.
        return AgentRun(
            status=WorkflowStatus.HUMAN_REVIEW_REQUIRED,
            message=(
                "Sentinel stopped after two invalid repair attempts. "
                "The original schedule remains unchanged."
            ),
            snapshot=snapshot,
            retrieval_query=retrieval_query,
            conflicts=conflicts,
            unscheduled_task_ids=unscheduled_ids,
            retrieved_rules=retrieved_rules,
            critic_result=critic_result,
            repair_attempts=tuple(attempts),
            final_validation=attempts[-1].validation_result if attempts else None,
            warnings=tuple(warnings),
        )