PawPal Sentinel
1. Original project: PawPal+
This project builds on PawPal+, a Streamlit app that helps a pet owner plan daily care tasks. PawPal+'s deterministic Scheduler (pawpal_system.py) packs each pet's tasks (walks, feeding, medication, play, grooming) into the owner's availability window by priority (high/medium/low) and preferred time, supports recurrence ("none"/"daily"/"weekly") so completed tasks automatically spawn their next occurrence, and detects same-pet and cross-pet scheduling conflicts. Owner/pet/task state persists as JSON (Owner.save_to_json / Owner.load_from_json), and the whole flow is driven through a Streamlit UI (app.py).

2. PawPal Sentinel summary
PawPal+ produces a schedule, but nothing verifies whether an AI-suggested change to that schedule is actually safe. PawPal Sentinel adds an AI-reviewed layer on top of the existing scheduler that inspects a draft plan, proposes fixes for conflicts, and enforces hard safety rules before anything is written back — answering one central question:

Can an AI improve a pet-care schedule without making unsafe or invalid changes?

3. Feature overview
RAG retriever (retriever.py) — keyword search over a local pet-care rules file (data/care_rules.md), builds its query only from structured schedule evidence (never free text), no AI call involved.
AI plan critic (plan_critic.py) — reviews a schedule snapshot plus deterministic conflict evidence and retrieved rules, and returns a strictly typed list of issues (or "no change needed"). Read-only — it cannot edit anything.
Repair agent (repair_agent.py) — proposes a minimal set of structured task-time changes to resolve the critic's issues, and can revise once using validator feedback.
Deterministic validator (schedule_validator.py) — the trust boundary. Ten hard-coded guardrail checks (schema, known task IDs, allowed actions, fixed/medication tasks unchanged, valid times, inside availability, no new conflicts, etc.) decide whether a proposal is ever shown to the owner.
Human approval (sentinel_service.py) — the owner explicitly approves or rejects a validated proposal; only approval can mutate the live schedule, and only the preferredTime field of move actions is ever changed.
Evaluation harness (evaluate.py) — runs the whole pipeline against a fixed set of scenarios (data/evaluation_scenarios.json) in fixture or live mode and scores it against expected outcomes.
4. Architecture
Mermaid source: diagrams/architecture.mmd (no rendered PNG has been generated yet — the .mmd file is the source of truth).

Data flow: the owner enters pets/tasks/availability in Streamlit, which the deterministic Scheduler.generatePlan() turns into a draft plan; that plan is snapshotted and checked for conflicts, which drive a retrieval query against care_rules.md. The AI Plan Critic reviews the snapshot, conflicts, and retrieved rules and reports issues; if there are real issues, the AI Repair Agent proposes task-time changes, which the deterministic Schedule Validator checks against ten guardrail rules — with at most one AI revision attempt if the first proposal fails. Only a validator-approved proposal reaches the owner for approval, and only an explicit approval mutates the live schedule (and only the preferredTime field of moved tasks). Every run and every owner decision is written to a structured JSONL log.

5. Setup
# Python 3.13
python -m venv .venv

# activate
source .venv/bin/activate        # macOS/Linux
.venv\Scripts\activate           # Windows

pip install -r requirements.txt

# copy and fill in your own Gemini API key
cp .env.example .env             # Windows: copy .env.example .env

# run the app
streamlit run app.py

# run the evaluation harness (fixture mode is deterministic/reproducible, no API key needed)
python evaluate.py --mode fixture
python evaluate.py --mode live   # requires a real GEMINI_API_KEY

# run the tests
pytest
Environment variables (see .env.example):

GEMINI_API_KEY — required for any live Gemini call (AI critic, repair agent, evaluate.py --mode live). Without it, GeminiAIClient raises AIConfigError and the app surfaces "AI review is disabled until GEMINI_API_KEY is configured."
PAWPAL_MODEL_NAME — optional. If unset or not a recognized Gemini model ID, the client falls back to the default model (gemini-3.1-flash-lite) and logs a warning.
6. End-to-end examples
Example 1 — Valid flexible repair
Input:
  Owner Priya, availability 06:30-18:30, pet Biscuit
  med-p1  Morning Medication  08:00  fixed     (medication)
  walk-p1 Morning Walk        08:00  flexible  (30 min)
  -> med-p1 and walk-p1 overlap at 08:00

Critic:    needs_revision — schedule_conflict on [med-p1, walk-p1]
Repair:    move walk-p1 08:00 -> 09:00, keep med-p1 untouched
Validator: attempt 1 passes all checks (fixed/medication unchanged, no new conflicts, inside availability)
Result:    awaiting_owner_approval
Example 2 — Unsafe fixed-task repair rejected
Input:
  Owner Aaliyah, availability 06:00-21:00, pet Nibbles
  med-a1  Vitamin Dose   09:00  fixed     (medication, daily)
  play-a1 Morning Play   09:00  flexible  (25 min)
  -> med-a1 and play-a1 overlap at 09:00

Critic:    needs_revision — fixed_task_conflict on [med-a1, play-a1]
Repair (attempt 1): move med-a1 09:00 -> 09:15
Validator: REJECTED (fixed_tasks_unchanged=false, medication_tasks_unchanged=false)
Repair (attempt 2, revise): defer_for_review both med-a1 and play-a1 instead of moving medication
Validator: passes (nothing moved) but yields no applicable schedule change
Result:    human_review_required — original schedule untouched
Example 3 — No repair needed
Input:
  Owner Helena, availability 08:00-16:00, pet Finn
  feed-h1  Breakfast Feeding  09:00  preferred (15 min)
  groom-h1 Feather Check      11:00  flexible  (20 min)
  -> no overlaps, nothing unscheduled

Critic:    no_change_needed — "No supported schedule issue was found."
Result:    no_repair_needed — repair/validator cycle is never invoked
7. Guardrail example
Input:            fixed medication task (med-a1, 09:00) overlaps a flexible play task (play-a1, 09:00)
AI proposal:       attempt 1 moves med-a1's time to 09:15
Validator behavior: rejects the proposal — fixed_tasks_unchanged and medication_tasks_unchanged both
                     evaluate to false; the validator error is fed back for one revision
Final result:      revision defers both tasks instead of moving medication -> human_review_required;
                    no live task time is ever changed
8. RAG impact on output quality
Without retrieval, the AI critic and repair agent receive the schedule snapshot and the general prompt constraints (e.g. "never move a fixed or medication task") but no task-specific PawPal care guidance beyond what is hard-coded into the system prompt. With retrieval enabled, a detected conflict drives build_retrieval_query() (retriever.py:194) to pull up to three matching sections out of data/care_rules.md via retrieve_rules() (retriever.py:129), which are then included in both the critic's and the repair agent's prompt context.

Before/after, using the documented trace (ai_interactions.md, Trace D):

A medication-conflict scenario retrieved the Medication Tasks, Feeding Tasks, and General Scheduling sections of care_rules.md. Those sections reinforce, in the pet-care domain's own language, that medication tasks are fixed and must never be rescheduled, and that flexible tasks should be moved to resolve an overlap instead. With that grounding in the prompt, the repair agent's first attempt (move, keep, move across the three conflicting tasks) passed the deterministic validator immediately — no revision cycle was needed.

Without the retrieved sections, the repair agent has only the general system prompt to go on when deciding which of several overlapping tasks is safe to move, which is where the baseline-vs-specialized-prompt comparison in model_card.md (§4/§7) shows a materially higher rate of unsafe proposals (5/5 unsafe under the generic prompt vs. 0/5 under the specialized, retrieval-aware prompt). The retrieval layer supplies the domain-specific "why" — grounding the model in task-type-specific rules rather than leaving it to infer scheduling safety from the schedule data alone.

In short: retrieval does not change whether the validator is the final authority (it always is), but it measurably reduces how often the repair agent needs a second attempt or produces a proposal the validator has to reject — see the full trace in ai_interactions.md and the metrics table in model_card.md.

9. Design decisions and tradeoffs
The existing PawPal+ scheduler stays fully deterministic — the AI never generates the draft plan, only reviews it.
The AI proposes changes; the deterministic ScheduleValidator decides. AI output is never trusted or applied on its own.
A validated proposal still requires explicit human approval before the live schedule is mutated.
The critic and repair agent are prompted and validated to never suggest diagnoses or medication dosages — medication tasks are structurally protected (fixed_tasks_unchanged/medication_tasks_unchanged checks).
Exactly one AI revision attempt is allowed per review (MAX_REPAIR_ATTEMPTS = 2 in sentinel_service.py) — a second validator failure always escalates to human review rather than retrying indefinitely.
The evaluation harness runs in fixture mode by default so scores are reproducible without depending on live model variance.
10. Testing summary
542 out of 542 tests passed (pytest -q, 2.70s). The fixture evaluation harness (reports/fixture_evaluation.json) scored 145/150 checks across 12 scenarios — a 96.67% reliability rate — with all 12 workflow-status outcomes matching expectations, 22/22 validator checks passed, 11/11 critic and repair outputs structurally valid, 11/11 issue-detection calls correct, 6/6 task-selection calls correct, and 0 scenario errors. No known failing tests or evaluation records as of this run.

11. Limitations
Model output can be inconsistent between runs; the deterministic validator exists specifically to catch this.
The care-rules knowledge base (data/care_rules.md) is a small, local, hand-written file — not a comprehensive veterinary reference.
Scheduling only supports same-day availability windows; overnight windows are rejected.
The system gives no medical advice, diagnoses, or dosage recommendations.
No task is ever automatically deleted by the AI pipeline.
Human review remains necessary — no proposal is ever applied without explicit owner approval.
