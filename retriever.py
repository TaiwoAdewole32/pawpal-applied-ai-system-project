"""Care-rule retriever for PawPal Sentinel (Phase 3.2 / 3.3).

Reuses the "load, split by heading, keyword-score, return top sections"
retrieval pattern from the earlier DocuBot project without importing any
DocuBot class — this is a small, self-contained keyword retriever over a
single project-controlled Markdown file (`data/care_rules.md`).

This module never talks to an AI client and never mutates anything. Its only
job is: given a query string built from structured schedule evidence, return
the one to three most relevant rule sections so they can be inserted into the
critic/repair prompts later (Phase 4).

Safety notes (see PAWPAL_SENTINEL_IMPLEMENTATION_PLAN.md Phase 3.4):
- `rules_path` is a project-controlled default. Callers must never pass a
  model-supplied path through to this function.
- The query itself must be built only from structured evidence (task types,
  flexibility values, conflict/availability/issue labels) — never from raw
  task notes or raw AI output. `build_retrieval_query()` below enforces that
  by only accepting those structured shapes as input.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Sequence

DEFAULT_RULES_PATH = "data/care_rules.md"
MAX_TOP_K = 3
MIN_SCORE_THRESHOLD = 0.0
MAX_CONTENT_CHARS = 600

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_RULES_DIR = os.path.abspath(os.path.join(_MODULE_DIR, "data"))

_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = {
    "a", "an", "the", "and", "or", "is", "are", "must", "not", "to", "of",
    "in", "on", "for", "with", "be", "this", "that", "only", "may", "if",
    "it", "as", "by", "from", "will", "can", "cannot", "no", "never",
    "also", "any", "other", "than", "such", "these", "those", "its",
    "their", "they", "them", "have", "has", "had", "but", "unless", "same",
}


@dataclass(frozen=True)
class RetrievedRule:
    section: str
    content: str
    score: float


def _stem(token: str) -> str:
    """Very small plural-stripping stemmer (walks->walk, tasks->task)."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokenize(text: object) -> list[str]:
    if not isinstance(text, str):
        return []
    raw = _TOKEN_RE.findall(text.lower())
    return [_stem(t) for t in raw if t not in _STOPWORDS and len(t) > 2]


def _parse_sections(markdown_text: str) -> list[tuple[str, str]]:
    """Split Markdown into (heading, content) pairs, content attached to its heading."""
    headings = list(_HEADING_RE.finditer(markdown_text))
    sections: list[tuple[str, str]] = []
    for i, heading_match in enumerate(headings):
        title = heading_match.group(1).strip()
        start = heading_match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown_text)
        content = markdown_text[start:end].strip()
        sections.append((title, content))
    return sections


def _is_safe_rules_path(rules_path: object) -> bool:
    """Reject anything but a path that resolves inside the project's data/ dir.

    Enforces Phase 3.4's "do not load arbitrary file paths supplied by the
    model" / "use a project-controlled rules path" guardrails in code, not
    just by caller convention — a path-traversal string or an absolute path
    pointing elsewhere is rejected rather than opened.
    """
    if not isinstance(rules_path, str) or not rules_path:
        return False
    candidate = os.path.abspath(os.path.join(_MODULE_DIR, rules_path))
    try:
        return os.path.commonpath([candidate, _RULES_DIR]) == _RULES_DIR
    except ValueError:
        # Different drives on Windows, etc. -- can't be inside _RULES_DIR.
        return False


def _load_sections(rules_path: str) -> list[tuple[str, str]]:
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    return _parse_sections(text)


def _truncate(content: str) -> str:
    if len(content) <= MAX_CONTENT_CHARS:
        return content
    return content[:MAX_CONTENT_CHARS].rstrip() + "..."


def _section_token_sets(sections: list[tuple[str, str]]) -> list[set[str]]:
    return [set(_tokenize(title)) | set(_tokenize(content)) for title, content in sections]


def _document_frequencies(section_token_sets: list[set[str]]) -> dict[str, int]:
    """How many sections each token appears in — used to downweight generic terms."""
    df: dict[str, int] = {}
    for tokens in section_token_sets:
        for token in tokens:
            df[token] = df.get(token, 0) + 1
    return df


def retrieve_rules(
    query: object,
    rules_path: str = DEFAULT_RULES_PATH,
    top_k: int = MAX_TOP_K,
) -> list[RetrievedRule]:
    """Return the top `top_k` care-rule sections relevant to `query`.

    Refuses unsafe or meaningless input rather than guessing: a non-string,
    empty, or whitespace-only query returns no evidence, as does a `top_k`
    of zero or less. `top_k` is always capped at MAX_TOP_K regardless of
    what is requested. Never returns the whole document — only sections
    with a positive score, each truncated to MAX_CONTENT_CHARS.

    Scoring weights each overlapping token by 1/(number of sections it
    appears in), so a distinctive term like "medication" (found in exactly
    one section) outweighs several generic terms like "flexible" or "owner"
    that are shared across most sections — a plain overlap count would let
    those generic terms drown out the one section actually being asked
    about.
    """
    if not isinstance(query, str):
        return []
    if not isinstance(top_k, int) or top_k <= 0:
        return []
    if not _is_safe_rules_path(rules_path):
        return []

    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return []

    sections = _load_sections(rules_path)
    if not sections:
        return []

    effective_top_k = min(top_k, MAX_TOP_K)

    section_token_sets = _section_token_sets(sections)
    document_frequency = _document_frequencies(section_token_sets)

    scored: list[tuple[float, int, str, str]] = []  # (-score, index, title, content) for stable sort
    for index, (title, content) in enumerate(sections):
        overlap = query_tokens & section_token_sets[index]
        score = sum(1.0 / document_frequency[token] for token in overlap)
        if score > MIN_SCORE_THRESHOLD:
            scored.append((-score, index, title, content))

    scored.sort(key=lambda row: (row[0], row[1]))  # score desc (via negation), then document order

    return [
        RetrievedRule(section=title, content=_truncate(content), score=-neg_score)
        for neg_score, _index, title, content in scored[:effective_top_k]
    ]


def _conflict_task_ids(conflict: object) -> tuple[object, object]:
    """Duck-type a Conflict-like object (task_id_a/task_id_b attrs) or a 2-tuple."""
    task_id_a = getattr(conflict, "task_id_a", None)
    task_id_b = getattr(conflict, "task_id_b", None)
    if task_id_a is not None and task_id_b is not None:
        return task_id_a, task_id_b
    a, b = conflict[0], conflict[1]
    return a, b


def build_retrieval_query(
    snapshot,
    conflicts: Sequence = (),
    unscheduled_task_ids: Sequence[str] = (),
    availability_violation: bool = False,
    issue_labels: Sequence[str] = (),
) -> str:
    """Build a retrieval query from structured schedule evidence only.

    The application (not the owner and not the AI) constructs this query, and
    only from IDs, task types, flexibility values, and short issue labels the
    app itself already computed. There is no path from free-text task notes
    or raw model output into this function's inputs, so it cannot be steered
    by injected instructions — see Phase 2.7 and 3.4.
    """
    conflicts = conflicts or ()
    unscheduled_task_ids = unscheduled_task_ids or ()
    issue_labels = issue_labels or ()

    tasks_by_id = {t.task_id: t for t in snapshot.tasks}
    terms: list[str] = []

    for conflict in conflicts:
        try:
            conflict_ids = _conflict_task_ids(conflict)
        except (TypeError, IndexError, KeyError):
            # One malformed conflict entry shouldn't discard evidence from
            # every other (valid) conflict in the same batch.
            continue
        for task_id in conflict_ids:
            task = tasks_by_id.get(task_id)
            if task is not None:
                terms.append(task.task_type)
                terms.append(task.flexibility)
        terms.append("conflict")

    for task_id in unscheduled_task_ids:
        task = tasks_by_id.get(task_id)
        if task is not None:
            terms.append(task.task_type)
    if unscheduled_task_ids:
        terms.append("unscheduled")

    if availability_violation:
        terms.append("availability")
        terms.append("window")

    for label in issue_labels:
        terms.append(str(label).replace("_", " "))

    return " ".join(terms).strip()