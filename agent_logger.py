"""Structured JSONL action logging for PawPal Sentinel (Phase 5.4).

The logger records observable workflow evidence and outcomes only. It does not
record prompts, model chain-of-thought, task notes, owner details, pet medical
or food data, API keys, environment variables, or live Python objects.

Logging is deliberately separated from the AI and scheduler layers. A caller
may inject a different log path for tests, while PawPalSentinel treats logging
failures as non-fatal so a read-only filesystem cannot crash Streamlit.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

DEFAULT_RUNTIME_LOG_PATH = "logs/runtime_agent_runs.jsonl"
MAX_LOG_RECORD_BYTES = 100_000
MAX_LOG_TEXT_CHARS = 500
MAX_VALIDATOR_ERRORS_PER_ATTEMPT = 30


class AgentLogError(RuntimeError):
    """Raised when a structured agent record cannot be safely written."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise AgentLogError("Logger clock must return a datetime value.")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_text(value: object, *, default: str = "") -> str:
    """Return bounded text without accepting arbitrary nested objects."""
    if not isinstance(value, str):
        return default
    return value.strip()[:MAX_LOG_TEXT_CHARS]


def _enum_value(value: object, *, default: str | None = None) -> str | None:
    raw = getattr(value, "value", value)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:MAX_LOG_TEXT_CHARS]
    return default


def _task_ids_from_snapshot(snapshot: object) -> list[str]:
    tasks = getattr(snapshot, "tasks", ()) if snapshot is not None else ()
    result: list[str] = []
    for task in tasks or ():
        task_id = _safe_text(getattr(task, "task_id", None))
        if task_id:
            result.append(task_id)
    return result


def _conflict_records(conflicts: object) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for conflict in conflicts or ():
        task_a = _safe_text(getattr(conflict, "task_id_a", None))
        task_b = _safe_text(getattr(conflict, "task_id_b", None))
        if task_a and task_b and task_a != task_b:
            records.append({"task_ids": [task_a, task_b], "type": "overlap"})
    return records


def _rule_sections(rules: object) -> list[str]:
    sections: list[str] = []
    seen: set[str] = set()
    for rule in rules or ():
        section = _safe_text(getattr(rule, "section", None))
        if section and section not in seen:
            seen.add(section)
            sections.append(section)
    return sections


def _critic_issue_types(critic_result: object) -> list[str]:
    if critic_result is None:
        return []
    result: list[str] = []
    for issue in getattr(critic_result, "issues", ()) or ():
        issue_type = _enum_value(getattr(issue, "issue_type", None))
        if issue_type:
            result.append(issue_type)
    return result


def _repair_attempt_records(attempts: object) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for attempt in attempts or ():
        attempt_number = getattr(attempt, "attempt", None)
        if isinstance(attempt_number, bool) or not isinstance(attempt_number, int):
            continue

        repair_result = getattr(attempt, "repair_result", None)
        proposed_task_ids: list[str] = []
        proposed_actions: list[str] = []
        for change in getattr(repair_result, "proposed_changes", ()) or ():
            task_id = _safe_text(getattr(change, "task_id", None))
            action = _safe_text(getattr(change, "action", None))
            if task_id:
                proposed_task_ids.append(task_id)
            if action:
                proposed_actions.append(action)

        validation = getattr(attempt, "validation_result", None)
        validator_valid = getattr(validation, "valid", False) is True
        raw_errors = getattr(validation, "errors", ()) or ()
        validator_errors = [
            _safe_text(error)
            for error in list(raw_errors)[:MAX_VALIDATOR_ERRORS_PER_ATTEMPT]
            if _safe_text(error)
        ]

        records.append(
            {
                "attempt": attempt_number,
                "proposed_task_ids": proposed_task_ids,
                "proposed_actions": proposed_actions,
                "validator_valid": validator_valid,
                "validator_errors": validator_errors,
            }
        )
    return records


def build_agent_run_record(
    run: object,
    *,
    prompt_version: str,
    timestamp: datetime | None = None,
) -> dict[str, object]:
    """Build an allowlisted, JSON-safe log record from an AgentRun-like object."""
    if run is None:
        raise AgentLogError("run must not be None.")
    if not isinstance(prompt_version, str) or not prompt_version.strip():
        raise AgentLogError("prompt_version must be a non-empty string.")

    snapshot = getattr(run, "snapshot", None)
    critic_result = getattr(run, "critic_result", None)
    status = _enum_value(getattr(run, "status", None), default="failed")
    critic_status = _enum_value(
        getattr(critic_result, "status", None),
        default=None,
    )

    record: dict[str, object] = {
        "timestamp": _iso_utc(timestamp or _utc_now()),
        "prompt_version": prompt_version.strip()[:MAX_LOG_TEXT_CHARS],
        "schedule_version": _safe_text(getattr(snapshot, "version", None)) or None,
        "draft_task_ids": _task_ids_from_snapshot(snapshot),
        "conflicts": _conflict_records(getattr(run, "conflicts", ())),
        "unscheduled_task_ids": [
            _safe_text(task_id)
            for task_id in (getattr(run, "unscheduled_task_ids", ()) or ())
            if _safe_text(task_id)
        ],
        "retrieved_rule_sections": _rule_sections(
            getattr(run, "retrieved_rules", ())
        ),
        "critic_status": critic_status,
        "critic_issues": _critic_issue_types(critic_result),
        "repair_attempts": _repair_attempt_records(
            getattr(run, "repair_attempts", ())
        ),
        "final_status": status,
    }
    return record


def _validate_json_tree(value: object) -> None:
    """Reject non-JSON values and non-finite numbers before filesystem writes."""
    stack = [value]
    while stack:
        current = stack.pop()
        if current is None or isinstance(current, (str, bool, int)):
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                raise AgentLogError("Log record contains a non-finite number.")
            continue
        if isinstance(current, list):
            stack.extend(current)
            continue
        if isinstance(current, dict):
            if not all(isinstance(key, str) for key in current):
                raise AgentLogError("Log record keys must be strings.")
            stack.extend(current.values())
            continue
        raise AgentLogError(
            f"Log record contains unsupported value type {type(current).__name__}."
        )


class AgentLogger:
    """Append one compact workflow record per line to a JSONL file."""

    def __init__(
        self,
        log_path: str | os.PathLike[str] = DEFAULT_RUNTIME_LOG_PATH,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not isinstance(log_path, (str, os.PathLike)):
            raise TypeError("log_path must be a string or path-like value.")
        path_text = os.fspath(log_path)
        if not path_text.strip():
            raise ValueError("log_path must not be empty.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self.log_path = Path(path_text)
        self.clock = clock

    def log_run(self, run: object, *, prompt_version: str) -> dict[str, object]:
        """Serialize and append one run, returning the exact written record."""
        record = build_agent_run_record(
            run,
            prompt_version=prompt_version,
            timestamp=self.clock(),
        )
        self.write_record(record)
        return record

    def write_record(self, record: Mapping[str, object]) -> None:
        """Append an allowlisted record atomically enough for one-process use."""
        if not isinstance(record, Mapping):
            raise AgentLogError("record must be a mapping.")
        normalized = dict(record)
        _validate_json_tree(normalized)
        try:
            line = json.dumps(
                normalized,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise AgentLogError(
                f"Log record could not be serialized ({type(exc).__name__})."
            ) from None

        encoded = (line + "\n").encode("utf-8")
        if len(encoded) > MAX_LOG_RECORD_BYTES:
            raise AgentLogError(
                f"Log record exceeds the {MAX_LOG_RECORD_BYTES}-byte safety limit."
            )

        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
                stream.write("\n")
        except OSError as exc:
            raise AgentLogError(
                f"Agent log could not be written ({type(exc).__name__})."
            ) from None