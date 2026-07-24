# AI Interactions Log

> **Stretch features only.** Only fill in the sections that apply to stretch features you attempted. If you did not attempt a stretch feature, leave its section blank or delete it. This file is not required for the core project.

---

## Agent Workflow (SF7)

> Document your experience using an AI agent (e.g., Cursor Agent, Claude, Copilot) to make multi-step changes autonomously.

**What task did you give the agent?**

<!-- Describe the goal you asked the agent to accomplish -->
I asked the AI coding assistant to make the scheduler do more than sort, filter, detect conflicts, and handle recurrence. I wanted the scheduler to generate a daily plan based on real constraints like priority, task duration, due date, completion status, and the owner's available care window.

**What did the agent do?**

The agent helped implement and refine the scheduling logic in `pawpal_system.py`. The main advanced algorithmic feature was the available-window scheduling behavior in `Scheduler.generatePlan()`. This method chooses pending tasks that are due today or overdue, sorts them by priority, checks whether they fit within the remaining available time, and separates tasks that cannot be scheduled into `unscheduledTasks`. The agent also helped add or refine `_fits_window()`, which checks whether a task's preferred time and duration fit inside the owner's available care window. In addition, the agent helped connect these backend behaviors to `app.py`, where the Streamlit UI displays the generated daily plan, unscheduled tasks, and conflict warnings. The agent also helped update `main.py` so the CLI demo shows sorting, conflict detection, recurring task creation, filtering, and daily plan generation.

<!-- List the steps the agent took (files edited, commands run, etc.) -->

- `pawpal_system.py`: Added and refined the scheduler logic, including `generatePlan()`, `_fits_window()`, `sort_by_time()`, `sortTasksByPriority()`, `filterTasks()`, `completeTask()`, `detectConflicts()`, and `getConflictWarnings()`.
- `app.py`: Connected the Streamlit UI to the scheduler so users can add/edit/remove pets, add/edit/delete tasks, sort/filter queued tasks, mark tasks done, generate a schedule, view unscheduled tasks, and see conflict warnings.
- `main.py`: Added a CLI demo that creates pets and tasks, adds tasks out of order, shows sorting, checks conflicts, completes recurring tasks, filters tasks, and prints the generated daily plan.
- `README.md`: Documented the system design, core classes, algorithmic features, advanced available-window scheduling capability, and demo walkthrough.
- `test_pawpal.py`: Added tests for sorting, recurrence, conflict detection, generated plans, owner availability, task editing/removal, and edge cases.


**What did you have to verify or fix manually?**

<!-- Describe anything the agent got wrong or that required human review -->

I manually reviewed the AI suggestions to make sure the scheduler stayed understandable and did not become too complex for the project scope. I checked that the generated plan still sorted tasks by time after priority-based selection, verified that recurring tasks created only one next occurrence, and tested that conflict warnings did not crash the app. I also reviewed the README wording so the advanced algorithmic capability was clearly documented.

---

## Prompt Comparison (SF11)

> Compare two different prompts (or two different models) on the same task.

| | Option A | Option B |
|-|----------|----------|
| **Model / tool used** | | |
| **Prompt** | | |
| **Response summary** | | |
| **What was useful** | | |
| **Problems noticed** | | |
| **Decision** | | |

**Which approach did you use in your final implementation and why?**

<!-- Your conclusion -->
