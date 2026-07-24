# PawPal+ (Module 2 Project)

You are building **PawPal+**, a Streamlit app that helps a pet owner plan care tasks for their pet.

## Scenario

A busy pet owner needs help staying consistent with pet care. They want an assistant that can:

- Track pet care tasks (walks, feeding, meds, enrichment, grooming, etc.)
- Consider constraints (time available, priority, owner preferences)
- Produce a daily plan and explain why it chose that plan

Your job is to design the system first (UML), then implement the logic in Python, then connect it to the Streamlit UI.

## What you will build

Your final app should:

- Let a user enter basic owner + pet info
- Let a user add/edit tasks (duration + priority at minimum)
- Generate a daily schedule/plan based on constraints and priorities
- Display the plan clearly (and ideally explain the reasoning)
- Include tests for the most important scheduling behaviors

## Getting started

### Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Suggested workflow

1. Read the scenario carefully and identify requirements and edge cases.
2. Draft a UML diagram (classes, attributes, methods, relationships).
3. Convert UML into Python class stubs (no logic yet).
4. Implement scheduling logic in small increments.
5. Add tests to verify key behaviors.
6. Connect your logic to the Streamlit UI in `app.py`.
7. Refine UML so it matches what you actually built.

## 🖥️ Sample Output

Paste a sample of your app's CLI or Streamlit output here so a reader can see what a generated plan looks like:

--- All tasks sorted by time (added out of order) (7) ---
  [HIGH] Walk Buddy (walk) | 30 min @ 7:00 AM | Pet: Buddy | Due: 2026-07-03 (daily) | Status: pending
  [MEDIUM] Bath Buddy (bath) | 25 min @ 7:00 AM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [MEDIUM] Groom Mittens (groom) | 20 min @ 9:30 AM | Pet: Mittens | Due: 2026-07-03 (weekly) | Status: pending
  [MEDIUM] Feed Mittens (feed) | 15 min @ 12:00 PM | Pet: Mittens | Due: 2026-07-03 (daily) | Status: pending
  [LOW] Buddy Lunchtime (play) | 20 min @ 12:05 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [HIGH] Evening Feed (feed) | 10 min @ 5:00 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [LOW] Play with Buddy (play) | 20 min @ 6:00 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending

--- Tasks sorted by priority first, then time (7) ---
  [HIGH] Walk Buddy (walk) | 30 min @ 7:00 AM | Pet: Buddy | Due: 2026-07-03 (daily) | Status: pending
  [HIGH] Evening Feed (feed) | 10 min @ 5:00 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [MEDIUM] Bath Buddy (bath) | 25 min @ 7:00 AM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [MEDIUM] Groom Mittens (groom) | 20 min @ 9:30 AM | Pet: Mittens | Due: 2026-07-03 (weekly) | Status: pending
  [MEDIUM] Feed Mittens (feed) | 15 min @ 12:00 PM | Pet: Mittens | Due: 2026-07-03 (daily) | Status: pending
  [LOW] Buddy Lunchtime (play) | 20 min @ 12:05 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [LOW] Play with Buddy (play) | 20 min @ 6:00 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending

>> Checking for scheduling conflicts ...
   WARNING [cross-pet] 'Feed Mittens' (Mittens, 12:00 PM–12:15 PM) overlaps with 'Buddy Lunchtime' (Buddy, 12:05 PM–12:25 PM)
   WARNING [same-pet] 'Walk Buddy' (Buddy, 7:00 AM–7:30 AM) overlaps with 'Bath Buddy' (Buddy, 7:00 AM–7:25 AM)

>> Completing 'Walk Buddy' (daily) and 'Groom Mittens' (weekly) ...
   Spawned: 'Walk Buddy' due 2026-07-04 (daily)
   Spawned: 'Groom Mittens' due 2026-07-10 (weekly)

--- Completed tasks (2) ---
  [HIGH] Walk Buddy (walk) | 30 min @ 7:00 AM | Pet: Buddy | Due: 2026-07-03 (daily) | Status: done
  [MEDIUM] Groom Mittens (groom) | 20 min @ 9:30 AM | Pet: Mittens | Due: 2026-07-03 (weekly) | Status: done

--- Pending tasks (includes next-day spawns) (7) ---
  [LOW] Play with Buddy (play) | 20 min @ 6:00 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [MEDIUM] Feed Mittens (feed) | 15 min @ 12:00 PM | Pet: Mittens | Due: 2026-07-03 (daily) | Status: pending
  [HIGH] Evening Feed (feed) | 10 min @ 5:00 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [MEDIUM] Bath Buddy (bath) | 25 min @ 7:00 AM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [LOW] Buddy Lunchtime (play) | 20 min @ 12:05 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [HIGH] Walk Buddy (walk) | 30 min @ 7:00 AM | Pet: Buddy | Due: 2026-07-04 (daily) | Status: pending
  [MEDIUM] Groom Mittens (groom) | 20 min @ 9:30 AM | Pet: Mittens | Due: 2026-07-10 (weekly) | Status: pending

--- Buddy's pending tasks (5) ---
  [LOW] Play with Buddy (play) | 20 min @ 6:00 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [HIGH] Evening Feed (feed) | 10 min @ 5:00 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [MEDIUM] Bath Buddy (bath) | 25 min @ 7:00 AM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [LOW] Buddy Lunchtime (play) | 20 min @ 12:05 PM | Pet: Buddy | Due: 2026-07-03 | Status: pending
  [HIGH] Walk Buddy (walk) | 30 min @ 7:00 AM | Pet: Buddy | Due: 2026-07-04 (daily) | Status: pending

--- Mittens' pending tasks (2) ---
  [MEDIUM] Feed Mittens (feed) | 15 min @ 12:00 PM | Pet: Mittens | Due: 2026-07-03 (daily) | Status: pending
  [MEDIUM] Groom Mittens (groom) | 20 min @ 9:30 AM | Pet: Mittens | Due: 2026-07-10 (weekly) | Status: pending

Daily plan for Alice's pets — 5 task(s), 1 hour and 30 minutes of 12 hours used:

Buddy (Golden Retriever):
  7:00 AM -> Bath Buddy (25 min) [priority: medium]
  12:05 PM -> Buddy Lunchtime (20 min) [priority: low]
  5:00 PM -> Evening Feed (10 min) [priority: high]
  6:00 PM -> Play with Buddy (20 min) [priority: low]

Mittens (Cat):
  12:00 PM -> Feed Mittens (15 min) [priority: medium]

```

## 🧪 Testing PawPal+

```bash
# Run the full test suite:
pytest

# Run with coverage:
pytest --cov
```

The tests cover the main backend logic for PawPal+. They check task completion, adding tasks to the scheduler and pet, filtering by completion status and pet name, recurring daily and weekly tasks, plan generation, sorting by time and priority, conflict detection, task editing, pet methods, and owner availability calculations.

Confidence Level: ⭐⭐⭐⭐

Sample test output:
```
============================================================================ 56 passed in 0.06s =============================================================================

## 📐 Smarter Scheduling

> Fill in once you've implemented scheduling logic.

| Feature | Method(s) | Notes |
|---------|-----------|-------|
| Task sorting | sortTasksByPriority(), sort_by_time(), generatePlan() | Tasks can be sorted by priority from high to low, and the final daily plan is sorted by preferred time so it appears in schedule order.  |
| Filtering | filterTasks(), generatePlan(), _fits_window() | Tasks can be filtered by completion status and pet name. The scheduler also skips completed tasks, future tasks, tasks that exceed remaining time, and tasks outside the owner’s available window. |
| Conflict handling | detectConflicts(), getConflictWarnings()| The scheduler checks whether task time windows overlap and can return conflict warnings for same-pet or cross-pet conflicts.  |
| Recurring tasks | completeTask(), spawn_next() |  When a daily or weekly task is completed, the scheduler creates the next occurrence with the same task details and a new due date. |

## Features 
Owner and Pet Management
* The user can create or update an owner profile with a name and available care window.
* The app calculates the owner's available time in minutes and displays it in a readable format.
* The user can add pets with name, species, breed, age, food type, medication, and energy level.
* Existing pets can be edited or removed from the Streamlit UI.
* Removing a pet also removes that pet's tasks from the scheduler.

Task Management
* The user can add tasks with a title, type, duration, priority, assigned pet, preferred time, due date, and recurrence.
* Tasks are stored in the scheduler and also registered under the assigned pet.
* Each task has a unique taskId, which allows the app to edit or delete the correct task.
* The user can mark tasks as done, edit task details, or delete tasks from the queued task list.
* Completed tasks are excluded from future generated schedules and conflict checks.

Sorting and Filtering
* Scheduler.sort_by_time() sorts tasks from earliest to latest preferred time.
* Scheduler.sortTasksByPriority() sorts tasks from high priority to low priority.
* Scheduler.filterTasks() lets the UI filter tasks by completion status and pet name.
* The Streamlit UI allows the user to sort queued tasks by time, priority, or insertion order.
* The Streamlit UI allows the user to filter tasks by all/pending/completed status and by specific pet.

Daily Schedule Generation
* Scheduler.generatePlan() builds a daily plan from pending tasks that are due today or overdue.
* The scheduler prioritizes high-priority tasks first when deciding what fits in the available time.
* The final daily plan is displayed in chronological order using sort_by_time()
* Tasks that do not fit because of time limits or the owner's availability window are placed in unscheduledTasks
* Scheduler.explainPlan() creates a readable explanation grouped by pet.

Recurring Tasks
* Tasks can be non-recurring, daily, or weekly.
* When a daily task is marked complete, completeTask() uses _spawn_next() to create the next occurrence for the following day.
* When a weekly task is marked complete, the next occurrence is created one week later.
* The new recurring task keeps the same title, task type, duration, priority, pet, preferred time, recurrence, and notes.
* The scheduler prevents double-spawning by returning nothing if the same completed task is completed again.

Conflict Detection
* Scheduler.detectConflicts() returns tasks whose time windows overlap.
* Scheduler.getConflictWarnings() returns readable warning messages for overlapping tasks.
* Conflict warnings identify whether the conflict is a same-pet conflict or a cross-pet conflict.
* Adjacent tasks that touch but do not overlap are allowed.
* Completed tasks are ignored during conflict detection.
* The Streamlit UI displays conflict warnings after the user generates a schedule.

Data Persistence
* `Owner.save_to_json(filepath="data.json")` writes the owner's name, care window, preferences, pets, and the scheduler's tasks to a JSON file.
* `Owner.load_from_json(filepath="data.json")` rebuilds an `Owner` (with its pets and tasks correctly re-linked) from that file, or returns `None` if the file doesn't exist yet or can't be parsed.
* Every mutation in the Streamlit UI — creating/updating the owner, adding/editing/removing a pet, and adding/editing/marking done/deleting a task — immediately calls `save_to_json()`, so `data.json` always reflects the current state, including deletions.
* On startup, `app.py` calls `Owner.load_from_json()` once if no owner is in `st.session_state` yet, so pets and tasks survive restarting the app rather than only surviving within one running session.
* Serialization uses a small custom `to_dict()`/`from_dict()` pair on `Pet` and `Task` rather than a library like marshmallow: `Priority` is converted via `.value`, `datetime.time`/`datetime.date` via `.isoformat()`, and each task stores its pet's `petId` (a new field on `Pet`, added the same way `taskId` was added to `Task`) instead of a nested pet object — this avoids serializing `Pet`/`Task`'s circular reference (`Pet.tasks` ↔ `Task.pet`) and avoids adding a new dependency for a data shape this small.
* Files touched for this feature: `pawpal_system.py` (new `petId` field on `Pet`; `to_dict`/`from_dict` on `Pet` and `Task`; `save_to_json`/`load_from_json` on `Owner`), `app.py` (loads `data.json` on startup, saves after every mutation), `.gitignore` (`data.json` excluded, since it's a generated runtime file), and `test_pawpal.py` (round-trip persistence tests).

Professional UI 
* The Streamlit UI (`app.py`) shows color-coded status via `st.success`/`st.warning`/`st.info`/`st.error` banners (green for done, orange for high-priority pending, red for conflicts) plus emoji priority badges (🔴 High, 🟠 Medium, 🟢 Low) from the `priority_badge()` helper).


## 📸 Demo Walkthrough

Describe your app in numbered steps so a reader can follow along without watching a video:

1. In Owner Setup, the user enters their name and chooses an available care window, such as 7:00 AM to 7:00 PM. The app saves the owner and displays the total available time.
2. In Add a Pet, the user adds one or more pets by entering details such as pet name, species, breed, age, energy level, food type, and medication.
3. The pet list appears under Your pets, where the user can expand a pet card to edit or remove that pet.
4. In Schedule a Task, the user creates care tasks for a selected pet. Each task includes a task title, task type, duration, priority, recurrence, preferred time, and due date.
5. The queued task section displays all created tasks as cards. The user can sort tasks by time or priority, filter by status or pet, mark a task done, edit a task, or delete a task.
6. When the user marks a daily or weekly recurring task as done, PawPal+ automatically creates the next occurrence with a new due date.
7. The user clicks Generate Schedule to create the daily plan. The scheduler selects pending tasks that are due today, prioritizes important tasks, checks whether each task fits in the owner's available time window, and displays the final plan in time order.
8. If a task cannot fit, it appears in the Not scheduled section.
9. If two task time windows overlap, the app displays a conflict warning. The warning explains which tasks overlap and whether the conflict is for the same pet or different pets.

**Screenshot or video** *(optional)*: <!-- Insert a screenshot or link to a demo video here -->
