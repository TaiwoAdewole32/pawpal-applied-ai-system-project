import datetime
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from pawpal_system import Pet, Priority, Task, Owner, Flexibility, format_time, format_duration
from ai_client import AIConfigError, GeminiAIClient
from sentinel_service import PawPalSentinel, WorkflowStatus, ApprovalStatus
from sentinel_models import build_schedule_snapshot

load_dotenv()

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="wide")
st.title("🐾 PawPal+")

# ── Cosmetic-only styling ────────────────────────────────────────────────────
# Scoped to (1) the two primary action buttons (Generate Schedule / Generate AI
# Review are the only `type="primary"` buttons in the app) and (2) sidebar text
# sizing. Purely visual: no widget keys, layout widths, or logic are touched.
st.markdown(
    """
    <style>
    /* Hide Streamlit's built-in chrome: the colored top decoration bar,
       the hamburger/menu toolbar, and the running-status widget. */
    div[data-testid="stDecoration"],
    div[data-testid="stToolbar"],
    div[data-testid="stStatusWidget"],
    #MainMenu {
        display: none !important;
    }

    /* Generate Schedule / Generate AI Review: make the primary CTA green */
    button[kind="primary"] {
        background-color: #2e7d32 !important;
        border-color: #2e7d32 !important;
        color: #ffffff !important;
    }
    button[kind="primary"]:hover {
        background-color: #1b5e20 !important;
        border-color: #1b5e20 !important;
        color: #ffffff !important;
    }
    button[kind="primary"]:active,
    button[kind="primary"]:focus {
        background-color: #1b5e20 !important;
        border-color: #1b5e20 !important;
        color: #ffffff !important;
    }

    /* Sidebar: larger text that better fills the existing (fixed) sidebar
       width, without changing the sidebar's own size. */
    section[data-testid="stSidebar"] h1 {
        font-size: 2rem !important;
    }
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] .stMarkdown,
    section[data-testid="stSidebar"] .stCaption {
        font-size: 1.15rem !important;
        line-height: 1.5 !important;
    }

    /* Sidebar navigation: icon-over-label buttons that fill the sidebar
       width, with a rounded highlight on the selected item and a hover
       state on the rest. Built on top of st.radio -- no widget swap, so
       session state, callbacks, and the section_renderers lookup below
       are all untouched. */
    section[data-testid="stSidebar"] div[data-testid="stRadio"] > label {
        display: none; /* hide the "Navigate" radio title */
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] {
        display: flex;
        flex-direction: column;
        gap: 0.6rem;
        width: 100%;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label {
        width: 100%;
        margin: 0 !important;
        padding: 1.1rem 0.5rem !important;
        border-radius: 14px !important;
        cursor: pointer;
        display: flex !important;
        justify-content: center;
        align-items: center;
        transition: background-color 0.15s ease;
    }
    /* Hide the native radio dot entirely (the input itself, and the
       decorative circle rendered next to the label text) -- the highlight
       communicates selection instead, matching the reference icon-nav
       style. Targeted structurally (sibling of the markdown container)
       rather than by hashed emotion class names, which change across
       Streamlit builds. */
    section[data-testid="stSidebar"] div[role="radiogroup"] label input[type="radio"],
    section[data-testid="stSidebar"] div[role="radiogroup"] label div:has(> div[data-testid="stMarkdownContainer"]) > div:not([data-testid="stMarkdownContainer"]) {
        display: none !important;
        width: 0 !important;
        height: 0 !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label p {
        font-size: 1.05rem !important;
        line-height: 1.3 !important;
        margin: 0 !important;
        text-align: center;
        white-space: pre-line; /* turns the "icon\nlabel" string into two lines */
    }
    /* The icon is the first line produced by the pre-line break above;
       enlarging just that line makes the icons dominate each button
       without also blowing up the label text below them. */
    section[data-testid="stSidebar"] div[role="radiogroup"] label p::first-line {
        font-size: 2.1rem !important;
        line-height: 1.7 !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
        background-color: rgba(151, 71, 255, 0.12);
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
        background-color: #7c3aed;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) p {
        color: #ffffff !important;
        font-weight: 600;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

DATA_FILE = "data.json"

SENTINEL_RUN_KEY = "sentinel_run"
SENTINEL_VERSION_KEY = "sentinel_snapshot_version"
SENTINEL_STATUS_KEY = "sentinel_status"
SENTINEL_FINAL_KEY = "sentinel_final_confirmation"
SENTINEL_HISTORY_KEY = "sentinel_history"
SENTINEL_NOTICE_KEY = "sentinel_notice"
SENTINEL_IDLE_STATUS = "idle"
MAX_SENTINEL_HISTORY = 20

if "ai_client" not in st.session_state:
    try:
        st.session_state.ai_client = GeminiAIClient()
        st.session_state.ai_status = "ready"
        st.session_state.ai_config_warning = getattr(
            st.session_state.ai_client,
            "configuration_warning",
            None,
        )
    except AIConfigError as exc:
        st.session_state.ai_client = None
        st.session_state.ai_status = str(exc)
        st.session_state.ai_config_warning = None


def initialize_sentinel_state(state=None) -> None:
    """Create Sentinel UI state without overwriting valid pending or history data."""
    target = st.session_state if state is None else state
    target.setdefault(SENTINEL_RUN_KEY, None)
    target.setdefault(SENTINEL_VERSION_KEY, None)
    target.setdefault(SENTINEL_STATUS_KEY, SENTINEL_IDLE_STATUS)
    target.setdefault(SENTINEL_FINAL_KEY, None)
    target.setdefault(SENTINEL_NOTICE_KEY, None)
    if not isinstance(target.get(SENTINEL_HISTORY_KEY), list):
        target[SENTINEL_HISTORY_KEY] = []


def clear_pending_sentinel_review(
    state=None,
    *,
    clear_confirmation: bool = True,
) -> None:
    """Invalidate stored AI state after schedule-relevant data changes.

    The private decision history remains available for logging/auditing, but the
    UI-facing final confirmation is cleared whenever the underlying owner, pet,
    or task data changes. Approval passes ``clear_confirmation=False`` so the
    new success confirmation survives the rerun.
    """
    target = st.session_state if state is None else state
    target[SENTINEL_RUN_KEY] = None
    target[SENTINEL_VERSION_KEY] = None
    target[SENTINEL_STATUS_KEY] = SENTINEL_IDLE_STATUS
    if clear_confirmation:
        target[SENTINEL_FINAL_KEY] = None


def _set_sentinel_notice(level: str, message: object, state=None) -> None:
    """Store one bounded message that survives a Streamlit rerun."""
    target = st.session_state if state is None else state
    normalized_level = level if level in {"success", "info", "warning", "error"} else "info"
    normalized_message = (
        message.strip()[:1_000]
        if isinstance(message, str) and message.strip()
        else "Sentinel completed the requested action."
    )
    target[SENTINEL_NOTICE_KEY] = {
        "level": normalized_level,
        "message": normalized_message,
    }


def _render_sentinel_notice() -> None:
    """Render and consume a one-time Sentinel notice."""
    notice = st.session_state.pop(SENTINEL_NOTICE_KEY, None)
    if not isinstance(notice, dict):
        return
    level = notice.get("level")
    message = notice.get("message")
    if not isinstance(message, str) or not message.strip():
        return
    renderer = {
        "success": st.success,
        "warning": st.warning,
        "error": st.error,
    }.get(level, st.info)
    renderer(message.strip())


def _append_sentinel_history(entry: object, state=None) -> None:
    """Append one bounded, JSON-like UI history item and cap retained entries."""
    if not isinstance(entry, dict):
        return
    target = st.session_state if state is None else state
    history = target.get(SENTINEL_HISTORY_KEY)
    if not isinstance(history, list):
        history = []
    history.append(dict(entry))
    target[SENTINEL_HISTORY_KEY] = history[-MAX_SENTINEL_HISTORY:]


def store_sentinel_run(run: object, state=None) -> None:
    """Store only the run, its reviewed version, and a normalized status value."""
    target = st.session_state if state is None else state

    raw_status = getattr(getattr(run, "status", None), "value", getattr(run, "status", None))
    status = raw_status if isinstance(raw_status, str) and raw_status else "failed"

    snapshot = getattr(run, "snapshot", None)
    raw_version = getattr(snapshot, "version", None)
    version = raw_version if isinstance(raw_version, str) and raw_version else None

    target[SENTINEL_RUN_KEY] = run
    target[SENTINEL_VERSION_KEY] = version
    target[SENTINEL_STATUS_KEY] = status


def run_sentinel_review(owner: Owner):
    """Execute one Sentinel review and persist its safe UI state."""
    clear_pending_sentinel_review()

    if not isinstance(owner, Owner):
        st.session_state[SENTINEL_STATUS_KEY] = WorkflowStatus.FAILED.value
        return None

    ai_client = st.session_state.get("ai_client")
    if ai_client is None:
        st.session_state[SENTINEL_STATUS_KEY] = WorkflowStatus.AI_UNAVAILABLE.value
        return None

    try:
        sentinel = PawPalSentinel(ai_client)
        run = sentinel.review_plan(owner)
    except Exception as exc:
        # Do not surface raw exception text because third-party SDK errors may
        # contain request details. The base PawPal+ workflow remains usable.
        st.session_state[SENTINEL_STATUS_KEY] = WorkflowStatus.FAILED.value
        st.error(
            "Sentinel review failed safely "
            f"({type(exc).__name__}). The original schedule was not changed."
        )
        return None

    store_sentinel_run(run)
    return run


def reject_pending_sentinel_review() -> str:
    """Reject a pending proposal, preserve task times, and record the decision."""
    run = st.session_state.get(SENTINEL_RUN_KEY)
    if run is None:
        clear_pending_sentinel_review()
        return "There is no pending Sentinel proposal to reject."

    message = "The proposal was rejected. The original schedule remains unchanged."
    decision_status = "owner_rejected"
    ai_client = st.session_state.get("ai_client")
    if ai_client is not None:
        try:
            result = PawPalSentinel(ai_client).reject(run)
            result_message = getattr(result, "message", None)
            raw_status = getattr(getattr(result, "status", None), "value", None)
            if isinstance(result_message, str) and result_message.strip():
                message = result_message.strip()[:1_000]
            if isinstance(raw_status, str) and raw_status:
                decision_status = raw_status
        except Exception as exc:
            message = (
                "The proposal was cleared, but its rejection could not be logged "
                f"({type(exc).__name__}). The original schedule remains unchanged."
            )
            decision_status = "rejection_logging_failed"

    _append_sentinel_history(
        {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": decision_status,
            "message": message,
            "changes": [],
        }
    )
    clear_pending_sentinel_review()
    return message



# ── Phase 6.3 and 6.4 shared display helpers ────────────────────────────────
# These helpers belong after the Phase 6.2 session-state/review helpers and
# before the existing PawPal+ form/rendering functions.

def _enum_text(value: object, default: str = "unknown") -> str:
    """Return a non-empty enum/string value without trusting arbitrary objects."""
    raw = getattr(value, "value", value)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return default


def _safe_text(value: object, default: str = "") -> str:
    """Return trimmed display text or a controlled fallback."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _format_snapshot_time(value: object) -> str:
    """Format strict snapshot HH:MM text as a readable 12-hour time."""
    if not isinstance(value, str):
        return "Unknown time"
    try:
        parsed = datetime.datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return "Invalid time"
    return format_time(parsed)


def _snapshot_task_lookup(run: object) -> dict[str, object]:
    """Build a task-ID lookup only from the exact reviewed snapshot."""
    snapshot = getattr(run, "snapshot", None)
    tasks = getattr(snapshot, "tasks", ()) if snapshot is not None else ()
    if not isinstance(tasks, (tuple, list)):
        return {}

    lookup: dict[str, object] = {}
    for task in tasks:
        task_id = _safe_text(getattr(task, "task_id", None))
        if task_id and task_id not in lookup:
            lookup[task_id] = task
    return lookup


# ── Phase 6.4: Render the validated AI critic report ────────────────────────
def _severity_renderer(severity: str):
    """Map the critic's typed severity to a Streamlit message renderer."""
    if severity == "high":
        return st.error
    if severity == "medium":
        return st.warning
    return st.info


def render_ai_critic_report(run: object) -> None:
    """Render user-facing findings without model confidence or RAG internals."""
    critic = getattr(run, "critic_result", None)
    if critic is None:
        return

    tasks_by_id = _snapshot_task_lookup(run)
    critic_status = _enum_text(getattr(critic, "status", None))
    summary = _safe_text(
        getattr(critic, "summary", None),
        "The review completed without a displayable summary.",
    )

    st.markdown("### AI Review Findings")
    with st.container(border=True):
        if critic_status == "no_change_needed":
            st.success(summary)
        else:
            st.write(summary)

        issues = getattr(critic, "issues", ()) or ()
        if not isinstance(issues, (tuple, list)):
            st.error("The validated issue collection was unavailable.")
            return
        if not issues:
            # When status is "no_change_needed" the summary above already
            # states this; a second identical success message is redundant.
            if critic_status != "no_change_needed":
                st.success("No supported schedule issue was found.")
            return

        for index, issue in enumerate(issues, start=1):
            issue_type = _enum_text(getattr(issue, "issue_type", None))
            severity = _enum_text(getattr(issue, "severity", None))
            renderer = _severity_renderer(severity)
            renderer(
                f"Issue {index}: {issue_type.replace('_', ' ').title()} "
                f"({severity.title()} severity)"
            )

            explanation = _bounded_display_text(
                getattr(issue, "explanation", None),
                "No displayable explanation was returned.",
                max_chars=1_000,
            )
            st.write(explanation)

            raw_task_ids = getattr(issue, "task_ids", ()) or ()
            affected_names: list[str] = []
            if isinstance(raw_task_ids, (tuple, list)):
                for task_id in raw_task_ids:
                    if not isinstance(task_id, str):
                        continue
                    task = tasks_by_id.get(task_id)
                    if task is None:
                        continue
                    affected_names.append(
                        _bounded_display_text(
                            getattr(task, "task_name", None),
                            "Unnamed task",
                            max_chars=200,
                        )
                    )
            if affected_names:
                st.caption("Affected task(s): " + ", ".join(affected_names))

            if index < len(issues):
                st.divider()


# ── Phase 6.5: Render only deterministically validated changes ──────────────
def _bounded_display_text(
    value: object,
    default: str = "",
    *,
    max_chars: int = 500,
) -> str:
    """Return bounded text so malformed display objects cannot flood the UI."""
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        max_chars = 500
    text = _safe_text(value, default)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _repair_attempts(run: object) -> tuple[object, ...]:
    """Return only well-shaped repair attempts from an AgentRun-like object."""
    raw_attempts = getattr(run, "repair_attempts", ()) or ()
    if not isinstance(raw_attempts, (tuple, list)):
        return ()
    return tuple(attempt for attempt in raw_attempts if attempt is not None)


def _validated_changes(run: object) -> tuple[object, ...]:
    """Return changes only when the final deterministic validation passed."""
    validation = getattr(run, "final_validation", None)
    if getattr(validation, "valid", False) is not True:
        return ()
    raw_changes = getattr(run, "validated_changes", ()) or ()
    if not isinstance(raw_changes, (tuple, list)):
        return ()
    return tuple(change for change in raw_changes if change is not None)


def _validated_move_changes(run: object) -> tuple[object, ...]:
    """Return only approved-action candidates that actually move a task."""
    return tuple(
        change
        for change in _validated_changes(run)
        if _enum_text(getattr(change, "action", None)) == "move"
    )


def render_validated_changes(run: object) -> bool:
    """Display only safe, validator-approved time moves.

    Raw repair-agent proposals are intentionally never rendered. This prevents
    invalid, stale, unsupported, or later-rejected suggestions from appearing as
    actionable user choices.
    """
    moves = _validated_move_changes(run)
    if not moves:
        return False

    tasks_by_id = _snapshot_task_lookup(run)
    st.markdown("### Validated Suggested Changes")
    with st.container(border=True):
        rendered = 0
        for change in moves:
            task_id = _safe_text(getattr(change, "task_id", None))
            task = tasks_by_id.get(task_id)
            if task is None:
                continue

            task_name = _bounded_display_text(
                getattr(task, "task_name", None),
                "Unnamed task",
                max_chars=200,
            )
            pet_name = _bounded_display_text(
                getattr(task, "pet_name", None),
                "Unknown pet",
                max_chars=200,
            )
            original_time = _format_snapshot_time(
                getattr(change, "original_time", None)
            )
            new_time = _format_snapshot_time(getattr(change, "new_time", None))
            reason = _bounded_display_text(
                getattr(change, "reason", None),
                "Move validated by the deterministic schedule guardrails.",
                max_chars=500,
            )

            st.markdown(f"**{task_name}** for {pet_name}")
            st.write(f"{original_time} → **{new_time}**")
            st.caption(reason)
            rendered += 1
            if rendered < len(moves):
                st.divider()

        if rendered == 0:
            return False

        st.success(
            "Only the changes shown above passed every deterministic guardrail. "
            "Nothing has been applied yet."
        )
    return True


# ── Phase 6.6: Deterministic validator evidence (eligibility only, no UI) ────
def _current_schedule_version_matches(owner: Owner, run: object) -> tuple[bool, str]:
    """Compare the live schedule fingerprint with the exact reviewed version."""
    snapshot = getattr(run, "snapshot", None)
    reviewed_version = _safe_text(getattr(snapshot, "version", None))
    stored_version = _safe_text(st.session_state.get(SENTINEL_VERSION_KEY))

    if not isinstance(owner, Owner):
        return False, "A valid owner is required to verify the current schedule version."
    if not reviewed_version:
        return False, "The reviewed schedule version is missing."
    if not stored_version:
        return False, "The stored Sentinel schedule version is missing."

    try:
        current_snapshot = build_schedule_snapshot(owner)
    except Exception as exc:
        return (
            False,
            "The current schedule version could not be rebuilt safely "
            f"({type(exc).__name__}).",
        )

    current_version = _safe_text(getattr(current_snapshot, "version", None))
    matches = (
        bool(current_version)
        and reviewed_version == stored_version
        and reviewed_version == current_version
    )
    if matches:
        return True, "The proposal matches the current schedule version."
    return (
        False,
        "The schedule changed after this review. Generate a new AI-reviewed plan "
        "before approval.",
    )


def _has_validated_move(run: object) -> bool:
    """Return True only when the workflow exposes at least one validated move."""
    changes = getattr(run, "validated_changes", ()) or ()
    if not isinstance(changes, (tuple, list)):
        return False
    return any(_enum_text(getattr(change, "action", None)) == "move" for change in changes)


def _compute_approval_eligibility(owner: Owner, run: object) -> bool:
    """Return whether Phase 6.7 may enable approval, without rendering anything.

    The returned value is display-level eligibility only. Phase 6.7 must still
    rebuild the snapshot, revalidate, and call the service approval boundary.
    """
    validation = getattr(run, "final_validation", None)
    if validation is None:
        return False

    valid_flag = getattr(validation, "valid", False) is True
    raw_checks = getattr(validation, "checks", {})
    if not isinstance(raw_checks, dict):
        valid_flag = False

    version_matches, _ = _current_schedule_version_matches(owner, run)
    status = _enum_text(getattr(run, "status", None))

    return (
        valid_flag
        and version_matches
        and status == WorkflowStatus.AWAITING_OWNER_APPROVAL.value
        and _has_validated_move(run)
    )



# ── Phase 6.7: Revalidate and apply only after explicit approval ─────────────
def _approval_change_records(run: object, changes: object) -> list[dict[str, str]]:
    """Create bounded display/history records from approved typed changes."""
    if not isinstance(changes, (tuple, list)):
        return []
    tasks_by_id = _snapshot_task_lookup(run)
    records: list[dict[str, str]] = []
    for change in changes:
        task_id = _safe_text(getattr(change, "task_id", None))
        if not task_id:
            continue
        task = tasks_by_id.get(task_id)
        task_name = _bounded_display_text(
            getattr(task, "task_name", None) if task is not None else None,
            task_id,
            max_chars=200,
        )
        records.append(
            {
                "task_id": task_id,
                "task_name": task_name,
                "action": _enum_text(getattr(change, "action", None)),
                "original_time": _safe_text(getattr(change, "original_time", None)),
                "new_time": _safe_text(getattr(change, "new_time", None)),
            }
        )
    return records


def approve_pending_sentinel_review(owner: Owner) -> tuple[bool, str, str]:
    """Call the service approval boundary and preserve the original on failure.

    PawPalSentinel.approve() rebuilds the latest snapshot, compares versions,
    revalidates, applies only normalized time moves, and saves atomically.  This
    UI helper never performs direct Task mutation.
    """
    run = st.session_state.get(SENTINEL_RUN_KEY)
    ai_client = st.session_state.get("ai_client")
    if not isinstance(owner, Owner):
        return False, "Approval requires a valid owner.", ApprovalStatus.FAILED.value
    if run is None:
        return False, "There is no pending Sentinel proposal to approve.", ApprovalStatus.NOT_APPROVABLE.value
    if ai_client is None:
        return False, "AI approval is unavailable because the AI client is not configured.", ApprovalStatus.FAILED.value

    try:
        result = PawPalSentinel(ai_client).approve(owner, run, data_file=DATA_FILE)
    except Exception as exc:
        return (
            False,
            f"Approval failed safely ({type(exc).__name__}). The original schedule was preserved.",
            ApprovalStatus.FAILED.value,
        )

    success = getattr(result, "success", False) is True
    status = _enum_text(getattr(result, "status", None), ApprovalStatus.FAILED.value)
    message = _bounded_display_text(
        getattr(result, "message", None),
        "The approval request completed without a displayable message.",
        max_chars=1_000,
    )

    if not success:
        # A stale proposal can never become valid again, so clear it. Other
        # failures remain visible for inspection and a possible retry.
        if status == ApprovalStatus.STALE_PROPOSAL.value:
            clear_pending_sentinel_review()
        return False, message, status

    try:
        owner.scheduler.generatePlan()
        final_snapshot = build_schedule_snapshot(owner)
    except Exception as exc:
        final_snapshot = getattr(result, "current_snapshot", None)
        extra_warning = (
            "The changes were saved, but the final plan could not be regenerated "
            f"for display ({type(exc).__name__})."
        )
    else:
        extra_warning = ""

    change_records = _approval_change_records(
        run,
        getattr(result, "applied_changes", ()),
    )
    warnings = [
        warning.strip()[:1_000]
        for warning in (getattr(result, "warnings", ()) or ())
        if isinstance(warning, str) and warning.strip()
    ]
    if extra_warning:
        warnings.append(extra_warning)

    confirmation = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": status,
        "message": message,
        "changes": change_records,
        "snapshot": final_snapshot,
        "warnings": warnings,
    }
    st.session_state[SENTINEL_FINAL_KEY] = confirmation
    _append_sentinel_history(
        {
            "timestamp": confirmation["timestamp"],
            "status": status,
            "message": message,
            "changes": change_records,
        }
    )
    clear_pending_sentinel_review(clear_confirmation=False)
    return True, message, status


def render_final_schedule_confirmation(owner: Owner) -> None:
    """Display a compact saved-result confirmation without repeating the plan."""
    confirmation = st.session_state.get(SENTINEL_FINAL_KEY)
    if not isinstance(confirmation, dict):
        return

    st.markdown("### Approved Changes Saved")
    message = _bounded_display_text(
        confirmation.get("message"),
        "The approved changes were applied and saved.",
        max_chars=1_000,
    )
    st.success(message)

    for warning in confirmation.get("warnings", ()) or ():
        if isinstance(warning, str) and warning.strip():
            st.warning(warning.strip()[:1_000])

    changes = confirmation.get("changes")
    if isinstance(changes, list) and changes:
        with st.container(border=True):
            for change in changes:
                if not isinstance(change, dict):
                    continue
                if _safe_text(change.get("action")) != "move":
                    continue
                name = _bounded_display_text(
                    change.get("task_name"), "Task", max_chars=200
                )
                old_time = _format_snapshot_time(change.get("original_time"))
                new_time = _format_snapshot_time(change.get("new_time"))
                st.write(f"**{name}:** {old_time} → {new_time}")

    st.button(
        "View Updated Schedule",
        key="sentinel_view_updated_schedule",
        use_container_width=True,
        on_click=_set_active_section,
        args=("Generate Schedule",),
    )


# ── Phase 6.8: Controlled UI error states ───────────────────────────────────
def _safe_workflow_message(run: object, status: str) -> str:
    """Convert workflow results, including old raw SDK errors, into safe UI text."""
    raw_message = _bounded_display_text(getattr(run, "message", None), max_chars=1_000)
    lowered = raw_message.lower()

    if status == WorkflowStatus.AI_UNAVAILABLE.value:
        if "model" in lowered and any(token in lowered for token in ("invalid", "format", "argument")):
            return (
                "AI review is unavailable because the configured Gemini model name "
                "was invalid. PawPal+ preserved the original schedule."
            )
        if any(token in lowered for token in ("timeout", "timed out", "deadline exceeded")):
            return "The AI request timed out. The original schedule was preserved; try again shortly."
        if "missing gemini_api_key" in lowered:
            return "AI review is disabled until GEMINI_API_KEY is configured."
        if "rate limit" in lowered or "429" in lowered:
            return "The AI service rate limit was reached. The original schedule was preserved."
        return raw_message or "AI review is temporarily unavailable. The original schedule was preserved."

    if status == WorkflowStatus.INVALID_AI_OUTPUT.value:
        return (
            "The AI returned an invalid structured response, so Sentinel stopped "
            "before validation or mutation. The original schedule was preserved."
        )
    if status == WorkflowStatus.FAILED.value:
        return raw_message or "Sentinel stopped safely. The original schedule was preserved."
    if status == WorkflowStatus.HUMAN_REVIEW_REQUIRED.value:
        attempts = _repair_attempts(run)
        final_validation = getattr(run, "final_validation", None)
        if len(attempts) >= 2 and getattr(final_validation, "valid", True) is not True:
            return (
                "Sentinel stopped after two invalid repair attempts. No change was "
                "applied; review the original schedule manually."
            )
        return raw_message or "This schedule requires human review; no changes were applied."
    if status == WorkflowStatus.NO_REPAIR_NEEDED.value:
        return raw_message or "No conflict or supported schedule issue requires repair."
    if status == WorkflowStatus.AWAITING_OWNER_APPROVAL.value:
        return raw_message or "A validated repair is ready for owner approval."
    return raw_message or "Sentinel completed with an unknown status; no changes were applied."


# ── Updated Sentinel workflow renderer ──────────────────────────────────────
# This coordinator renders Phases 6.3 through 6.8 in workflow order.
def _review_overview_counts(owner: Owner, run: object) -> tuple[int, int, int]:
    """Return bounded draft counts without rendering the schedule a second time."""
    scheduled = getattr(getattr(owner, "scheduler", None), "dailyPlan", ()) or ()
    unscheduled = getattr(run, "unscheduled_task_ids", ()) or ()
    conflicts = getattr(run, "conflicts", ()) or ()
    scheduled_count = len(scheduled) if isinstance(scheduled, (tuple, list)) else 0
    unscheduled_count = len(unscheduled) if isinstance(unscheduled, (tuple, list)) else 0
    conflict_count = len(conflicts) if isinstance(conflicts, (tuple, list)) else 0
    return scheduled_count, unscheduled_count, conflict_count


def render_review_overview(owner: Owner, run: object) -> None:
    """Show a compact review summary instead of repeating the full draft plan."""
    scheduled_count, unscheduled_count, conflict_count = _review_overview_counts(
        owner, run
    )
    col1, col2, col3 = st.columns(3)
    col1.metric("Draft tasks", scheduled_count)
    col2.metric("Unscheduled", unscheduled_count)
    col3.metric("Conflicts", conflict_count)
    st.caption(
        "The detailed task list remains in Generate Schedule. This page shows "
        "only AI findings, validated changes, and approval guardrails."
    )


def render_sentinel_state_summary(owner: Owner) -> None:
    """Render a compact, stage-aware Sentinel review with no internal RAG UI."""
    run = st.session_state.get(SENTINEL_RUN_KEY)
    if run is None:
        return

    status = st.session_state.get(SENTINEL_STATUS_KEY, SENTINEL_IDLE_STATUS)
    message = _safe_workflow_message(run, status)
    critic = getattr(run, "critic_result", None)

    # When there is nothing to repair, the AI Review Findings panel below
    # already states this (via the critic's own "no_change_needed" summary).
    # Showing the same "no issues" message again here would just repeat it,
    # so the top-level banner is skipped for that one case.
    skip_top_banner = (
        status == WorkflowStatus.NO_REPAIR_NEEDED.value and critic is not None
    )

    if not skip_top_banner:
        if status in {
            WorkflowStatus.NO_REPAIR_NEEDED.value,
            WorkflowStatus.AWAITING_OWNER_APPROVAL.value,
        }:
            st.success(message)
        elif status in {
            WorkflowStatus.HUMAN_REVIEW_REQUIRED.value,
            WorkflowStatus.AI_UNAVAILABLE.value,
        }:
            st.warning(message)
        else:
            st.error(message)

    for warning in getattr(run, "warnings", ()) or ():
        if isinstance(warning, str) and warning.strip():
            st.warning(warning.strip()[:1_000])

    if getattr(run, "snapshot", None) is not None:
        render_review_overview(owner, run)

    if status in {WorkflowStatus.AI_UNAVAILABLE.value, WorkflowStatus.FAILED.value}:
        return

    critic = getattr(run, "critic_result", None)
    if critic is not None:
        render_ai_critic_report(run)
    elif status == WorkflowStatus.INVALID_AI_OUTPUT.value:
        st.info(
            "No AI findings were shown because the response did not pass "
            "structured-output validation."
        )
        return

    if status == WorkflowStatus.NO_REPAIR_NEEDED.value:
        # Intentionally no proposed-repair panel when there are no changes.
        return

    if status == WorkflowStatus.HUMAN_REVIEW_REQUIRED.value:
        validation = getattr(run, "final_validation", None)
        if validation is None or getattr(validation, "valid", False) is True:
            st.warning(
                "Sentinel could not produce a movable repair that can be safely "
                "approved. Keep the current schedule and review the conflict manually."
            )
        return

    if status == WorkflowStatus.INVALID_AI_OUTPUT.value:
        return

    if status != WorkflowStatus.AWAITING_OWNER_APPROVAL.value:
        return

    has_displayable_changes = render_validated_changes(run)
    approval_ready = (
        _compute_approval_eligibility(owner, run)
        if getattr(run, "final_validation", None) is not None
        else False
    )
    approval_ready = approval_ready and has_displayable_changes

    if not has_displayable_changes:
        st.error(
            "No deterministically validated time move is available for approval. "
            "The original schedule remains unchanged."
        )
        return

    approve_col, reject_col = st.columns(2)
    with approve_col:
        approve_clicked = st.button(
            "Approve Validated Changes",
            key="sentinel_approve_suggested_changes",
            disabled=not approval_ready,
            use_container_width=True,
        )
    with reject_col:
        reject_clicked = st.button(
            "Reject and Keep Original",
            key="sentinel_reject_suggested_changes",
            use_container_width=True,
        )

    if approve_clicked:
        with st.status(
            "Revalidating and saving approved changes...",
            expanded=True,
        ) as approval_status_box:
            st.write("Rebuilding the current schedule fingerprint.")
            st.write("Running deterministic guardrails again.")
            success, approval_message, approval_status = (
                approve_pending_sentinel_review(owner)
            )
            if success:
                approval_status_box.update(
                    label="Validated changes were saved.",
                    state="complete",
                    expanded=False,
                )
            else:
                approval_status_box.update(
                    label="No changes were applied.",
                    state="error",
                    expanded=True,
                )

        if success:
            st.rerun()
        elif approval_status == ApprovalStatus.STALE_PROPOSAL.value:
            _set_sentinel_notice("warning", approval_message)
            st.rerun()
        elif approval_status == ApprovalStatus.SAVE_FAILED.value:
            st.error(
                approval_message
                or "The approved changes could not be saved. The original schedule was restored."
            )
        else:
            st.error(approval_message)

    if reject_clicked:
        rejection_message = reject_pending_sentinel_review()
        _set_sentinel_notice("info", rejection_message)
        st.rerun()


initialize_sentinel_state()


def time_picker(label: str, default: datetime.time, key_prefix: str) -> datetime.time:
    """Render an Hour / Minute / AM-PM selectbox trio and return the combined time."""
    st.caption(label)
    c1, c2, c3 = st.columns(3)
    default_hour12 = default.hour % 12 or 12
    with c1:
        hour = st.selectbox("Hour", list(range(1, 13)), index=default_hour12 - 1, key=f"{key_prefix}_hour")
    with c2:
        minute = st.selectbox(
            "Minute", [f"{m:02d}" for m in range(60)], index=default.minute, key=f"{key_prefix}_minute"
        )
    with c3:
        ampm = st.selectbox("AM/PM", ["AM", "PM"], index=0 if default.hour < 12 else 1, key=f"{key_prefix}_ampm")
    hour24 = (hour % 12) + (12 if ampm == "PM" else 0)
    return datetime.time(hour24, int(minute))


def priority_badge(priority: Priority) -> str:
    return {"high": "🔴 High", "medium": "🟠 Medium", "low": "🟢 Low"}.get(priority.value, priority.value)


def flexibility_badge(flexibility: Flexibility) -> str:
    return {
        "fixed": "🔒 Fixed",
        "preferred": "🕓 Preferred",
        "flexible": "↔️ Flexible",
    }.get(flexibility.value, flexibility.value)


def task_detail_line(t: Task) -> str:
    line = (
        f"{format_time(t.preferredTime)} — {t.taskName} "
        f"({format_duration(t.durationMinutes)}) · {priority_badge(t.priority)} · "
        f"{flexibility_badge(t.flexibility)} · Due: {t.dueDate}"
    )
    if t.recurrence != "none":
        line += f" · Recurs: {t.recurrence.capitalize()}"
    if t.notes:
        line += f" · Notes: {t.notes}"
    return line


def render_task_card(t: Task, owner: "Owner", key_prefix: str) -> None:
    """Render one queued task with guarded completion, deletion, and editing."""
    with st.container(border=True):
        st.markdown(f"**{t.taskName}** ({t.taskType}) · {t.pet.name}")

        if t.completed:
            st.success(f"✅ Done — {task_detail_line(t)}")
        elif t.priority == Priority.HIGH:
            st.warning(task_detail_line(t))
        else:
            st.info(task_detail_line(t))

        bcol1, bcol2 = st.columns([1, 1])
        with bcol1:
            if not t.completed and st.button(
                "Mark Done", key=f"done_{key_prefix}_{t.taskId}"
            ):
                next_task = None
                try:
                    next_task = owner.scheduler.completeTask(t)
                    _invalidate_generated_schedule(owner)
                    clear_pending_sentinel_review()
                    if not _safe_save_owner(owner):
                        t.completed = False
                        if next_task is not None:
                            owner.scheduler.removeTask(next_task)
                        return
                except (TypeError, ValueError) as exc:
                    t.completed = False
                    if next_task is not None:
                        owner.scheduler.removeTask(next_task)
                    st.error(f"The task could not be completed: {exc}")
                    return

                if next_task is not None:
                    st.success(
                        f"Next '{next_task.taskName}' scheduled for "
                        f"{next_task.dueDate}."
                    )
                st.rerun()

        with bcol2:
            if st.button(
                "🗑️ Delete Task",
                key=f"delete_{key_prefix}_{t.taskId}",
            ):
                tasks = owner.scheduler.tasks
                task_index = tasks.index(t) if t in tasks else len(tasks)
                pet_index = t.pet.tasks.index(t) if t in t.pet.tasks else len(t.pet.tasks)
                daily_index = (
                    owner.scheduler.dailyPlan.index(t)
                    if t in owner.scheduler.dailyPlan
                    else None
                )
                unscheduled_index = (
                    owner.scheduler.unscheduledTasks.index(t)
                    if t in owner.scheduler.unscheduledTasks
                    else None
                )
                owner.scheduler.removeTask(t)
                _invalidate_generated_schedule(owner)
                clear_pending_sentinel_review()
                if not _safe_save_owner(owner):
                    owner.scheduler.tasks.insert(
                        min(task_index, len(owner.scheduler.tasks)), t
                    )
                    if t not in t.pet.tasks:
                        t.pet.tasks.insert(min(pet_index, len(t.pet.tasks)), t)
                    if daily_index is not None and t not in owner.scheduler.dailyPlan:
                        owner.scheduler.dailyPlan.insert(
                            min(daily_index, len(owner.scheduler.dailyPlan)), t
                        )
                    if (
                        unscheduled_index is not None
                        and t not in owner.scheduler.unscheduledTasks
                    ):
                        owner.scheduler.unscheduledTasks.insert(
                            min(
                                unscheduled_index,
                                len(owner.scheduler.unscheduledTasks),
                            ),
                            t,
                        )
                    return
                st.success(f"Task '{t.taskName}' deleted.")
                st.rerun()

        with st.expander("Edit"):
            recurrence_options = ["None", "Daily", "Weekly"]
            lower_options = ["none", "daily", "weekly"]
            default_recur_index = (
                lower_options.index(t.recurrence)
                if t.recurrence in lower_options
                else 0
            )
            flexibility_options = ["Fixed", "Preferred", "Flexible"]
            with st.form(key=f"edit_task_form_{key_prefix}_{t.taskId}"):
                ecol1, ecol2 = st.columns(2)
                with ecol1:
                    edit_task_name = st.text_input(
                        "Task title",
                        value=t.taskName,
                        max_chars=150,
                        key=f"edit_tname_{key_prefix}_{t.taskId}",
                    )
                    edit_task_type = st.text_input(
                        "Task type",
                        value=t.taskType,
                        max_chars=100,
                        key=f"edit_ttype_{key_prefix}_{t.taskId}",
                    )
                    edit_duration = st.number_input(
                        "Duration (minutes)",
                        min_value=1,
                        max_value=1_440,
                        value=int(t.durationMinutes),
                        key=f"edit_tdur_{key_prefix}_{t.taskId}",
                    )
                    edit_recurrence = st.selectbox(
                        "Recurrence",
                        recurrence_options,
                        index=default_recur_index,
                        key=f"edit_trec_{key_prefix}_{t.taskId}",
                    )
                with ecol2:
                    edit_priority = st.selectbox(
                        "Priority",
                        ["high", "medium", "low"],
                        index=["high", "medium", "low"].index(t.priority.value),
                        key=f"edit_tprio_{key_prefix}_{t.taskId}",
                    )
                    edit_flexibility = st.selectbox(
                        "Flexibility",
                        flexibility_options,
                        index=["fixed", "preferred", "flexible"].index(
                            t.flexibility.value
                        ),
                        key=f"edit_tflex_{key_prefix}_{t.taskId}",
                        help=(
                            "Protected task types such as medication are always "
                            "saved as fixed."
                        ),
                    )
                    edit_pref_time = time_picker(
                        "Preferred time",
                        t.preferredTime,
                        f"edit_ttime_{key_prefix}_{t.taskId}",
                    )
                    edit_due_date = st.date_input(
                        "Due date",
                        value=t.dueDate,
                        key=f"edit_tdue_{key_prefix}_{t.taskId}",
                    )
                edit_task_save = st.form_submit_button(
                    "Save Changes", use_container_width=True
                )

            if edit_task_save:
                normalized_name = edit_task_name.strip()
                normalized_type = edit_task_type.strip()
                if not normalized_name:
                    st.error("Task title must not be empty.")
                    return
                if not normalized_type:
                    st.error("Task type must not be empty.")
                    return

                try:
                    updated_task = Task(
                        taskId=t.taskId,
                        taskName=normalized_name,
                        taskType=normalized_type,
                        durationMinutes=int(edit_duration),
                        priority=Priority(edit_priority),
                        pet=t.pet,
                        preferredTime=edit_pref_time,
                        flexibility=Flexibility(edit_flexibility.lower()),
                        recurrence=edit_recurrence.lower(),
                        dueDate=edit_due_date,
                        completed=t.completed,
                        notes=t.notes,
                    )
                except (TypeError, ValueError) as exc:
                    st.error(f"The task could not be updated: {exc}")
                    return

                owner.scheduler.editTask(updated_task)
                _invalidate_generated_schedule(owner)
                clear_pending_sentinel_review()
                if not _safe_save_owner(owner):
                    owner.scheduler.editTask(t)
                    return
                st.success(f"Task '{normalized_name}' updated.")
                st.rerun()


if "owner" not in st.session_state:
    loaded_owner = Owner.load_from_json(DATA_FILE)
    if isinstance(loaded_owner, Owner):
        st.session_state.owner = loaded_owner


NAV_SECTIONS = (
    "Owner Setup",
    "Pets",
    "Schedule a Task",
    "Generate Schedule",
    "AI Review",
)

NAV_SECTION_ICONS: dict[str, str] = {
    "Owner Setup": "👤",
    "Pets": "🐾",
    "Schedule a Task": "📝",
    "Generate Schedule": "📅",
    "AI Review": "🤖",
}


def _nav_option_label(section: str) -> str:
    """Icon-over-label text for one sidebar nav entry (icon\\nlabel).

    The returned string is only ever used for display via `format_func`;
    the underlying value stored in session state and looked up in
    `section_renderers` is always the plain section name.
    """
    icon = NAV_SECTION_ICONS.get(section, "•")
    return f"{icon}\n{section}"


def _active_owner() -> Owner | None:
    owner_value = st.session_state.get("owner")
    return owner_value if isinstance(owner_value, Owner) else None


def _set_active_section(label: str) -> None:
    """Widget callback that safely changes the sidebar destination."""
    if label in NAV_SECTIONS:
        st.session_state["pawpal_active_section"] = label


def _render_missing_owner_guard() -> bool:
    if _active_owner() is not None:
        return False
    st.info("Create the owner profile before using this section.")
    st.button(
        "Go to Owner Setup",
        use_container_width=True,
        on_click=_set_active_section,
        args=("Owner Setup",),
    )
    return True


def _safe_save_owner(owner: Owner) -> bool:
    try:
        owner.save_to_json(DATA_FILE)
    except (OSError, TypeError, ValueError) as exc:
        st.error(
            "The latest changes could not be saved safely "
            f"({type(exc).__name__})."
        )
        return False
    return True




def _invalidate_generated_schedule(owner: Owner) -> None:
    """Clear cached plan lists after owner, pet, or task data changes."""
    if not isinstance(owner, Owner):
        return
    scheduler = getattr(owner, "scheduler", None)
    if scheduler is None:
        return
    scheduler.dailyPlan = []
    scheduler.unscheduledTasks = []
    scheduler.planGenerated = False

def render_owner_section() -> None:
    st.header("Owner Setup")
    st.caption("Set the owner's name and the daily pet-care availability window.")

    current = _active_owner()
    default_name = current.name if current is not None else "Jordan"
    default_start = current.startTime if current is not None else datetime.time(7, 0)
    default_end = current.endTime if current is not None else datetime.time(19, 0)

    with st.form("owner_setup_form"):
        owner_name = st.text_input("Owner name", value=default_name, max_chars=100)
        col1, col2 = st.columns(2)
        with col1:
            start_time = time_picker("Available from", default_start, "owner_start")
        with col2:
            end_time = time_picker("Available until", default_end, "owner_end")
        submitted = st.form_submit_button(
            "Save Owner",
            use_container_width=True,
        )

    if submitted:
        normalized_name = owner_name.strip() if isinstance(owner_name, str) else ""
        if not normalized_name:
            st.error("Owner name must not be empty.")
        elif end_time <= start_time:
            st.error(
                "Availability must end later than it starts. Overnight windows are "
                "not supported by the current Sentinel workflow."
            )
        else:
            created_new_owner = current is None
            previous_owner_values = None
            if created_new_owner:
                owner = Owner(
                    name=normalized_name,
                    startTime=start_time,
                    endTime=end_time,
                    preferences={},
                )
                st.session_state.owner = owner
            else:
                owner = current
                previous_owner_values = (
                    owner.name,
                    owner.startTime,
                    owner.endTime,
                    owner.scheduler.startTime,
                    owner.scheduler.timeAvailable,
                    owner.scheduler.ownerName,
                )
                owner.name = normalized_name
                owner.startTime = start_time
                owner.endTime = end_time
                owner.scheduler.startTime = start_time
                owner.scheduler.timeAvailable = owner.availableMinutes
                owner.scheduler.ownerName = normalized_name

            _invalidate_generated_schedule(owner)
            clear_pending_sentinel_review()
            if _safe_save_owner(owner):
                st.success(
                    f"Owner saved: {normalized_name}\n\n"
                    f"Availability: {format_time(start_time)} to {format_time(end_time)}\n\n"
                    f"Total available time: {format_duration(owner.getAvailableTime())}"
                )
            elif created_new_owner:
                st.session_state.pop("owner", None)
            elif previous_owner_values is not None:
                (
                    owner.name,
                    owner.startTime,
                    owner.endTime,
                    owner.scheduler.startTime,
                    owner.scheduler.timeAvailable,
                    owner.scheduler.ownerName,
                ) = previous_owner_values

    current = _active_owner()
    if current is not None:
        with st.container(border=True):
            st.markdown(f"**Active owner:** {current.name}")
            st.write(
                f"Availability: {format_time(current.startTime)} to "
                f"{format_time(current.endTime)}"
            )
            st.write(
                f"Daily care time: {format_duration(current.getAvailableTime())}"
            )


def render_pet_section() -> None:
    st.header("Pets")
    st.caption("Add a pet or update an existing pet profile.")
    if _render_missing_owner_guard():
        return
    owner = _active_owner()
    if owner is None:
        return

    with st.form("add_pet_form", clear_on_submit=False):
        col1, col2 = st.columns(2)
        with col1:
            pet_name = st.text_input("Pet name", value="Mochi", max_chars=100)
            species = st.selectbox("Species", ["Dog", "Cat", "Other"])
            breed = st.text_input("Breed (optional)", value="", max_chars=100)
            age = st.number_input(
                "Age (years)", min_value=0, max_value=100, value=2, step=1
            )
        with col2:
            energy_level = st.slider(
                "Energy level", min_value=1, max_value=10, value=5
            )
            food_type = st.text_input(
                "Food type", value="Dry Kibble", max_chars=150
            )
            medication = st.text_input(
                "Medication (or 'none')", value="none", max_chars=200
            )
        pet_submitted = st.form_submit_button(
            "Add Pet", use_container_width=True
        )

    if pet_submitted:
        normalized_name = pet_name.strip() if isinstance(pet_name, str) else ""
        normalized_food = food_type.strip() if isinstance(food_type, str) else ""
        normalized_medication = (
            medication.strip() if isinstance(medication, str) else ""
        )
        if not normalized_name:
            st.error("Pet name must not be empty.")
        elif not normalized_food:
            st.error("Food type must not be empty. Use 'unknown' when necessary.")
        elif not normalized_medication:
            st.error("Medication must not be empty. Use 'none' when applicable.")
        else:
            try:
                new_pet = Pet(
                    name=normalized_name,
                    species=species,
                    breed=breed.strip(),
                    age=int(age),
                    foodType=normalized_food,
                    medication=normalized_medication,
                    energyLevel=int(energy_level),
                )
                owner.addPet(new_pet)
                _invalidate_generated_schedule(owner)
                clear_pending_sentinel_review()
                if _safe_save_owner(owner):
                    st.success(f"Pet '{normalized_name}' added.")
                else:
                    owner.removePet(new_pet)
            except (TypeError, ValueError) as exc:
                st.error(f"The pet could not be added: {exc}")

    st.subheader("Saved Pets")
    if not owner.pets:
        st.info("No pets have been added yet.")
        return

    species_options = ["Dog", "Cat", "Other"]
    for pet in list(owner.pets):
        pet_key = _safe_text(getattr(pet, "petId", None), str(id(pet)))
        with st.expander(f"🐾 {pet.name}"):
            with st.form(key=f"edit_pet_form_{pet_key}"):
                ecol1, ecol2 = st.columns(2)
                with ecol1:
                    edit_name = st.text_input(
                        "Pet name",
                        value=pet.name,
                        max_chars=100,
                        key=f"edit_name_{pet_key}",
                    )
                    edit_species = st.selectbox(
                        "Species",
                        species_options,
                        index=next(
                            (
                                index
                                for index, option in enumerate(species_options)
                                if option.lower() == pet.species.lower()
                            ),
                            2,
                        ),
                        key=f"edit_species_{pet_key}",
                    )
                    edit_breed = st.text_input(
                        "Breed (optional)",
                        value=pet.breed,
                        max_chars=100,
                        key=f"edit_breed_{pet_key}",
                    )
                    edit_age = st.number_input(
                        "Age (years)",
                        min_value=0,
                        max_value=100,
                        value=int(pet.age),
                        key=f"edit_age_{pet_key}",
                    )
                with ecol2:
                    edit_energy = st.slider(
                        "Energy level",
                        min_value=1,
                        max_value=10,
                        value=int(pet.energyLevel),
                        key=f"edit_energy_{pet_key}",
                    )
                    edit_food = st.text_input(
                        "Food type",
                        value=pet.foodType,
                        max_chars=150,
                        key=f"edit_food_{pet_key}",
                    )
                    edit_medication = st.text_input(
                        "Medication (or 'none')",
                        value=pet.medication,
                        max_chars=200,
                        key=f"edit_med_{pet_key}",
                    )
                save_clicked = st.form_submit_button(
                    "Save Changes", use_container_width=True
                )

            if save_clicked:
                normalized_name = edit_name.strip()
                normalized_food = edit_food.strip()
                normalized_medication = edit_medication.strip()
                if not normalized_name:
                    st.error("Pet name must not be empty.")
                elif not normalized_food:
                    st.error("Food type must not be empty.")
                elif not normalized_medication:
                    st.error("Medication must not be empty.")
                else:
                    previous_values = {
                        "name": pet.name,
                        "species": pet.species,
                        "breed": pet.breed,
                        "age": pet.age,
                        "energyLevel": pet.energyLevel,
                        "foodType": pet.foodType,
                        "medication": pet.medication,
                    }
                    try:
                        pet.updatePet("name", normalized_name)
                        pet.updatePet("species", edit_species)
                        pet.updatePet("breed", edit_breed.strip())
                        pet.updatePet("age", int(edit_age))
                        pet.updatePet("energyLevel", int(edit_energy))
                        pet.updatePet("foodType", normalized_food)
                        pet.updatePet("medication", normalized_medication)
                        _invalidate_generated_schedule(owner)
                        clear_pending_sentinel_review()
                        if _safe_save_owner(owner):
                            st.success(f"Pet '{normalized_name}' updated.")
                            st.rerun()
                        for field_name, old_value in previous_values.items():
                            pet.updatePet(field_name, old_value)
                    except (TypeError, ValueError) as exc:
                        for field_name, old_value in previous_values.items():
                            try:
                                pet.updatePet(field_name, old_value)
                            except (TypeError, ValueError):
                                pass
                        st.error(f"The pet could not be updated: {exc}")

            if st.button(
                "Remove Pet",
                key=f"remove_pet_{pet_key}",
                use_container_width=True,
            ):
                removed_name = pet.name
                pet_index = owner.pets.index(pet)
                removed_tasks = [
                    task for task in owner.scheduler.tasks if task.pet is pet
                ]
                task_positions = {
                    task.taskId: owner.scheduler.tasks.index(task)
                    for task in removed_tasks
                }
                daily_positions = {
                    task.taskId: owner.scheduler.dailyPlan.index(task)
                    for task in removed_tasks
                    if task in owner.scheduler.dailyPlan
                }
                unscheduled_positions = {
                    task.taskId: owner.scheduler.unscheduledTasks.index(task)
                    for task in removed_tasks
                    if task in owner.scheduler.unscheduledTasks
                }

                owner.removePet(pet)
                owner.scheduler.dailyPlan = [
                    task for task in owner.scheduler.dailyPlan if task.pet is not pet
                ]
                owner.scheduler.unscheduledTasks = [
                    task
                    for task in owner.scheduler.unscheduledTasks
                    if task.pet is not pet
                ]
                _invalidate_generated_schedule(owner)
                clear_pending_sentinel_review()
                if _safe_save_owner(owner):
                    st.success(f"Pet '{removed_name}' and its tasks were removed.")
                    st.rerun()

                owner.pets.insert(min(pet_index, len(owner.pets)), pet)
                for task in sorted(
                    removed_tasks,
                    key=lambda item: task_positions[item.taskId],
                ):
                    owner.scheduler.tasks.insert(
                        min(
                            task_positions[task.taskId],
                            len(owner.scheduler.tasks),
                        ),
                        task,
                    )
                for task in removed_tasks:
                    if task.taskId in daily_positions:
                        owner.scheduler.dailyPlan.insert(
                            min(
                                daily_positions[task.taskId],
                                len(owner.scheduler.dailyPlan),
                            ),
                            task,
                        )
                    if task.taskId in unscheduled_positions:
                        owner.scheduler.unscheduledTasks.insert(
                            min(
                                unscheduled_positions[task.taskId],
                                len(owner.scheduler.unscheduledTasks),
                            ),
                            task,
                        )


def render_task_section() -> None:
    st.header("Schedule a Task")
    st.caption("Create, filter, edit, complete, or delete pet-care tasks.")
    if _render_missing_owner_guard():
        return
    owner = _active_owner()
    if owner is None:
        return
    if not owner.pets:
        st.info("Add at least one pet before scheduling a task.")
        st.button(
            "Go to Pets",
            use_container_width=True,
            on_click=_set_active_section,
            args=("Pets",),
        )
        return

    with st.form("add_task_form"):
        col1, col2 = st.columns(2)
        with col1:
            task_name = st.text_input(
                "Task title", value="Morning Walk", max_chars=150
            )
            task_type = st.text_input(
                "Task type", value="Exercise", max_chars=100
            )
            duration = st.number_input(
                "Duration (minutes)", min_value=1, max_value=1_440, value=20
            )
            recurrence = st.selectbox("Recurrence", ["None", "Daily", "Weekly"])
        with col2:
            priority_str = st.selectbox("Priority", ["high", "medium", "low"])
            flexibility_str = st.selectbox(
                "Flexibility",
                ["Fixed", "Preferred", "Flexible"],
                index=2,
                help=(
                    "Fixed tasks cannot move. Preferred tasks may move slightly. "
                    "Flexible tasks may move within owner availability. Protected "
                    "task types such as medication are always saved as fixed."
                ),
            )
            selected_pet = st.selectbox(
                "Pet",
                options=owner.pets,
                format_func=lambda pet: pet.name,
            )
            preferred_time = time_picker(
                "Preferred time", datetime.time(8, 0), "add_task_time"
            )
            due_date = st.date_input("Due date", value=datetime.date.today())
        task_submitted = st.form_submit_button(
            "Add Task", use_container_width=True
        )

    if task_submitted:
        normalized_name = task_name.strip() if isinstance(task_name, str) else ""
        normalized_type = task_type.strip() if isinstance(task_type, str) else ""
        if not normalized_name:
            st.error("Task title must not be empty.")
        elif not normalized_type:
            st.error("Task type must not be empty.")
        else:
            try:
                new_task = Task(
                    taskName=normalized_name,
                    taskType=normalized_type,
                    durationMinutes=int(duration),
                    priority=Priority(priority_str),
                    pet=selected_pet,
                    preferredTime=preferred_time,
                    flexibility=Flexibility(flexibility_str.lower()),
                    recurrence=recurrence.lower(),
                    dueDate=due_date,
                )
                owner.scheduler.addTask(new_task)
                _invalidate_generated_schedule(owner)
                clear_pending_sentinel_review()
                if _safe_save_owner(owner):
                    st.success(
                        f"Task '{normalized_name}' added as "
                        f"{flexibility_badge(new_task.flexibility)}."
                    )
                else:
                    owner.scheduler.removeTask(new_task)
            except (TypeError, ValueError) as exc:
                st.error(f"The task could not be added: {exc}")

    st.subheader("Queued Tasks")
    if not owner.scheduler.tasks:
        st.info("No tasks are queued yet.")
        return

    fcol1, fcol2, fcol3 = st.columns(3)
    with fcol1:
        sort_choice = st.radio(
            "Sort by", ["Time", "Priority", "None"], horizontal=True
        )
    with fcol2:
        status_choice = st.selectbox("Status", ["All", "Pending", "Completed"])
    with fcol3:
        pet_choice = st.selectbox("Pet filter", ["All"] + [p.name for p in owner.pets])

    completed_filter = (
        None if status_choice == "All" else status_choice == "Completed"
    )
    pet_filter = None if pet_choice == "All" else pet_choice
    visible_tasks = owner.scheduler.filterTasks(
        completed=completed_filter,
        petName=pet_filter,
    )

    if sort_choice == "Priority":
        order = {
            task.taskId: index
            for index, task in enumerate(owner.scheduler.sortTasksByPriority())
        }
        visible_tasks = sorted(
            visible_tasks,
            key=lambda task: order.get(task.taskId, len(order)),
        )
    elif sort_choice == "Time":
        visible_tasks = owner.scheduler.sort_by_time(visible_tasks)

    if not visible_tasks:
        st.info("No tasks match the selected filters.")
        return
    for task in visible_tasks:
        render_task_card(task, owner, key_prefix="queued")


def _schedule_grid_rows(tasks: list[Task]) -> list[dict[str, str]]:
    """Build one display row per task for the daily-plan grid, notes included
    only when the task actually has any."""
    rows = []
    for task in tasks:
        row = {
            "Time": format_time(task.preferredTime),
            "Task": task.taskName,
            "Pet": task.pet.name,
            "Duration": format_duration(task.durationMinutes),
            "Priority": priority_badge(task.priority),
            "Flexibility": flexibility_badge(task.flexibility),
            "Notes": task.notes if task.notes else "",
        }
        rows.append(row)
    return rows


def render_current_schedule(owner: Owner) -> None:
    scheduler = owner.scheduler
    if not getattr(scheduler, "planGenerated", False):
        st.info("Select Generate Schedule to create the current daily plan.")
        return

    st.markdown(f"**Today's Date:** {datetime.date.today().strftime('%A, %B %d, %Y')}")

    if not scheduler.dailyPlan:
        st.info("No tasks were scheduled for the current day.")
    else:
        total_minutes = sum(task.durationMinutes for task in scheduler.dailyPlan)
        st.success(
            f"{len(scheduler.dailyPlan)} task(s) scheduled. "
            f"{format_duration(total_minutes)} of "
            f"{format_duration(scheduler.timeAvailable)} used."
        )

        sort_choice = st.radio(
            "Sort by",
            ["Time", "Priority"],
            horizontal=True,
            key="generated_schedule_sort_choice",
        )
        if sort_choice == "Priority":
            ordered_tasks = scheduler.sortTasksByPriority(scheduler.dailyPlan)
        else:
            ordered_tasks = scheduler.sort_by_time(scheduler.dailyPlan)

        grid_df = pd.DataFrame(_schedule_grid_rows(ordered_tasks))
        st.dataframe(grid_df, hide_index=True, use_container_width=True)

    if scheduler.unscheduledTasks:
        st.markdown("### Not Scheduled")
        for task in scheduler.unscheduledTasks:
            st.warning(
                f"{task.taskName} for {task.pet.name}: insufficient time or outside "
                "the availability window."
            )

    conflict_warnings = scheduler.getConflictWarnings()
    if conflict_warnings:
        st.markdown("### Conflict Warnings")
        for warning in conflict_warnings:
            st.error(warning)
    else:
        st.success("No scheduling conflicts detected.")


def render_schedule_section() -> None:
    st.header("Generate Schedule")
    st.caption("Build and view the deterministic PawPal+ daily plan.")
    if _render_missing_owner_guard():
        return
    owner = _active_owner()
    if owner is None:
        return
    if not owner.scheduler.tasks:
        st.info("Add at least one task before generating a schedule.")
        st.button(
            "Go to Schedule a Task",
            use_container_width=True,
            on_click=_set_active_section,
            args=("Schedule a Task",),
        )
        return

    if st.button("Generate Schedule", type="primary", use_container_width=True):
        clear_pending_sentinel_review()
        with st.status("Building the daily schedule...", expanded=True) as status_box:
            st.write("Sorting eligible tasks by priority and preferred time.")
            st.write("Checking the owner availability window and capacity.")
            try:
                owner.scheduler.generatePlan()
            except Exception as exc:
                status_box.update(
                    label="Schedule generation stopped safely.",
                    state="error",
                    expanded=True,
                )
                st.error(
                    "The schedule could not be generated safely "
                    f"({type(exc).__name__})."
                )
            else:
                status_box.update(
                    label="Daily schedule generated.",
                    state="complete",
                    expanded=False,
                )

    render_current_schedule(owner)


def render_ai_generation_control(owner: Owner) -> None:
    ai_warning = st.session_state.get("ai_config_warning")
    if isinstance(ai_warning, str) and ai_warning.strip():
        st.warning(ai_warning.strip()[:1_000])

    ai_client = st.session_state.get("ai_client")
    if ai_client is None:
        ai_status = st.session_state.get("ai_status")
        safe_status = (
            ai_status.strip()[:1_000]
            if isinstance(ai_status, str) and ai_status.strip()
            else "AI review is not configured."
        )
        st.info(safe_status + " Normal scheduling remains available.")
        st.button(
            "Generate AI Review",
            disabled=True,
            use_container_width=True,
        )
        if st.button("Retry AI Configuration", use_container_width=True):
            st.session_state.pop("ai_client", None)
            st.session_state.pop("ai_status", None)
            st.session_state.pop("ai_config_warning", None)
            st.rerun()
        return

    if not owner.scheduler.tasks:
        st.info("Add at least one task before requesting an AI review.")
        st.button(
            "Generate AI Review",
            disabled=True,
            use_container_width=True,
        )
        return

    if st.button("Generate AI Review", type="primary", use_container_width=True):
        progress = st.progress(0, text="Preparing the current schedule...")
        progress.progress(20, text="Creating the deterministic draft...")
        progress.progress(45, text="Reviewing conflicts and task flexibility...")
        run = run_sentinel_review(owner)
        progress.progress(90, text="Checking all proposed changes with guardrails...")

        run_status = _enum_text(getattr(run, "status", None), "failed")
        if run is not None and run_status in {
            WorkflowStatus.NO_REPAIR_NEEDED.value,
            WorkflowStatus.AWAITING_OWNER_APPROVAL.value,
            WorkflowStatus.HUMAN_REVIEW_REQUIRED.value,
        }:
            progress.progress(100, text="Review complete.")
        else:
            progress.progress(100, text="Review stopped without changing the schedule.")


def render_ai_review_section() -> None:
    st.header("AI Review")
    st.caption(
        "Sentinel reviews a fresh draft, displays supported findings, and shows "
        "only deterministically validated time changes."
    )
    _render_sentinel_notice()
    if _render_missing_owner_guard():
        return
    owner = _active_owner()
    if owner is None:
        return

    render_ai_generation_control(owner)
    render_sentinel_state_summary(owner)
    render_final_schedule_confirmation(owner)


with st.sidebar:
    st.header("PawPal+")
    active_section = st.radio(
        "Navigate",
        NAV_SECTIONS,
        key="pawpal_active_section",
        format_func=_nav_option_label,
        label_visibility="collapsed",
    )
    st.divider()
    sidebar_owner = _active_owner()
    if sidebar_owner is None:
        st.caption("No owner profile saved")
    else:
        st.markdown(f"**Owner:** {sidebar_owner.name}")
        st.caption(
            f"{len(sidebar_owner.pets)} pet(s) · "
            f"{len(sidebar_owner.scheduler.tasks)} task(s)"
        )
    ai_ready = st.session_state.get("ai_client") is not None
    st.caption("AI review: ready" if ai_ready else "AI review: unavailable")


section_renderers = {
    "Owner Setup": render_owner_section,
    "Pets": render_pet_section,
    "Schedule a Task": render_task_section,
    "Generate Schedule": render_schedule_section,
    "AI Review": render_ai_review_section,
}
section_renderers.get(active_section, render_owner_section)()