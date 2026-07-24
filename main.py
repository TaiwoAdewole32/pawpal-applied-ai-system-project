from datetime import time, date
from pawpal_system import Pet, Priority, Task, Owner


def print_task_list(label: str, tasks: list) -> None:
    print(f"\n--- {label} ({len(tasks)}) ---")
    if not tasks:
        print("  (none)")
        return
    for task in tasks:
        print(f"  {task.getTaskSummary()}")


def main():
    owner = Owner(
        name="Alice",
        startTime=time(7, 0),
        endTime=time(19, 0),
        preferences={"morning": "walk", "afternoon": "feed"}
    )

    pet1 = Pet(name="Buddy",   species="Dog", breed="Golden Retriever", age=5, foodType="Dry", medication="None", energyLevel=7)
    pet2 = Pet(name="Mittens", species="Cat", breed="",                 age=3, foodType="Wet", medication="None", energyLevel=2)

    owner.addPet(pet1)
    owner.addPet(pet2)

    today = date.today()

    # Tasks added intentionally out of chronological order, mix of recurrence types
    task1 = Task(taskId="1", taskType="play",  taskName="Play with Buddy", durationMinutes=20, priority=Priority.LOW,    preferredTime=time(18,  0), pet=pet1, recurrence="none",   dueDate=today)
    task2 = Task(taskId="2", taskType="feed",  taskName="Feed Mittens",    durationMinutes=15, priority=Priority.MEDIUM, preferredTime=time(12,  0), pet=pet2, recurrence="daily",  dueDate=today)
    task3 = Task(taskId="3", taskType="walk",  taskName="Walk Buddy",      durationMinutes=30, priority=Priority.HIGH,   preferredTime=time(7,   0), pet=pet1, recurrence="daily",  dueDate=today)
    task4 = Task(taskId="4", taskType="groom", taskName="Groom Mittens",   durationMinutes=20, priority=Priority.MEDIUM, preferredTime=time(9,  30), pet=pet2, recurrence="weekly", dueDate=today)
    task5 = Task(taskId="5", taskType="feed",  taskName="Evening Feed",    durationMinutes=10, priority=Priority.HIGH,   preferredTime=time(17,  0), pet=pet1, recurrence="none",   dueDate=today)
    # Deliberate conflicts for conflict-detection demo:
    # task6: same pet (Buddy) + same start time as task3 → same-pet conflict
    task6 = Task(taskId="6", taskType="bath",  taskName="Bath Buddy",      durationMinutes=25, priority=Priority.MEDIUM, preferredTime=time(7,   0), pet=pet1, recurrence="none",   dueDate=today)
    # task7: different pet (Buddy) + overlaps task2's window (12:00–12:15) → cross-pet conflict
    task7 = Task(taskId="7", taskType="play",  taskName="Buddy Lunchtime", durationMinutes=20, priority=Priority.LOW,    preferredTime=time(12,  5), pet=pet1, recurrence="none",   dueDate=today)

    for task in [task1, task2, task3, task4, task5, task6, task7]:
        owner.scheduler.addTask(task)

    # ── Before any completions ────────────────────────────────────────────────
    print_task_list("All tasks sorted by time (added out of order)", owner.scheduler.sort_by_time())

    print_task_list(
        "Tasks sorted by priority first, then time",
        owner.scheduler.sortTasksByPriority(),
    )
    # ── Conflict detection ────────────────────────────────────────────────────
    print("\n>> Checking for scheduling conflicts ...")
    conflict_warnings = owner.scheduler.getConflictWarnings()
    if conflict_warnings:
        for w in conflict_warnings:
            print(f"   {w}")
    else:
        print("   No conflicts detected.")

    # ── Complete recurring tasks — next occurrences auto-spawn ────────────────
    print("\n>> Completing 'Walk Buddy' (daily) and 'Groom Mittens' (weekly) ...")
    next_walk  = owner.scheduler.completeTask(task3)
    next_groom = owner.scheduler.completeTask(task4)

    if next_walk:
        print(f"   Spawned: '{next_walk.taskName}' due {next_walk.dueDate} ({next_walk.recurrence})")
    if next_groom:
        print(f"   Spawned: '{next_groom.taskName}' due {next_groom.dueDate} ({next_groom.recurrence})")

    # ── Filter demos after completions ────────────────────────────────────────
    print_task_list("Completed tasks",                          owner.scheduler.filterTasks(completed=True))
    print_task_list("Pending tasks (includes next-day spawns)", owner.scheduler.filterTasks(completed=False))
    print_task_list("Buddy's pending tasks",                    owner.scheduler.filterTasks(completed=False, petName="Buddy"))
    print_task_list("Mittens' pending tasks",                   owner.scheduler.filterTasks(completed=False, petName="Mittens"))

    # ── Daily plan ────────────────────────────────────────────────────────────
    owner.scheduler.generatePlan()
    print(f"\n{owner.scheduler.explainPlan()}")


if __name__ == "__main__":
    main()
