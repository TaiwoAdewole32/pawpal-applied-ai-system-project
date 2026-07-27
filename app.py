import datetime
import streamlit as st
from pawpal_system import Pet, Priority, Task, Owner, Flexibility, format_time, format_duration

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")
st.title("🐾 PawPal+")

DATA_FILE = "data.json"


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
    """Render one queued task as a card with a status-colored banner, plus Mark Done / Edit controls."""
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
            if not t.completed:
                if st.button("Mark Done", key=f"done_{key_prefix}_{t.taskId}"):
                    next_t = owner.scheduler.completeTask(t)
                    if next_t:
                        st.success(f"Next '{next_t.taskName}' scheduled for {next_t.dueDate}.")
                    owner.save_to_json(DATA_FILE)
                    st.rerun()
        with bcol2:
            if st.button("🗑️ Delete Task", key=f"delete_{key_prefix}_{t.taskId}"):
                owner.scheduler.removeTask(t)
                owner.save_to_json(DATA_FILE)
                st.success(f"Task '{t.taskName}' deleted.")
                st.rerun()

        # Full-width expander (not confined to a narrow column) so the edit form has room to breathe.
        with st.expander("Edit"):
            recurrence_options = ["None", "Daily", "Weekly"]
            lower_options = ["none", "daily", "weekly"]
            default_recur_index = (
                lower_options.index(t.recurrence) if t.recurrence in lower_options else 0
            )
            flexibility_options = ["Fixed", "Preferred", "Flexible"]
            with st.form(key=f"edit_task_form_{key_prefix}_{t.taskId}"):
                ecol1, ecol2 = st.columns(2)
                with ecol1:
                    edit_task_name = st.text_input(
                        "Task Title", value=t.taskName, key=f"edit_tname_{key_prefix}_{t.taskId}"
                    )
                    edit_task_type = st.text_input(
                        "Task Type", value=t.taskType, key=f"edit_ttype_{key_prefix}_{t.taskId}"
                    )
                    edit_duration = st.number_input(
                        "Duration (in minutes)", min_value=1, max_value=240,
                        value=int(t.durationMinutes), key=f"edit_tdur_{key_prefix}_{t.taskId}",
                    )
                    edit_recurrence = st.selectbox(
                        "Recurrence", recurrence_options, index=default_recur_index,
                        key=f"edit_trec_{key_prefix}_{t.taskId}",
                    )
                with ecol2:
                    edit_priority = st.selectbox(
                        "Priority", ["high", "medium", "low"],
                        index=["high", "medium", "low"].index(t.priority.value),
                        key=f"edit_tprio_{key_prefix}_{t.taskId}",
                    )
                    edit_flexibility = st.selectbox(
                        "Flexibility", flexibility_options,
                        index=["fixed", "preferred", "flexible"].index(t.flexibility.value),
                        key=f"edit_tflex_{key_prefix}_{t.taskId}",
                        help="Fixed: must not move. Preferred: may move slightly with approval. "
                             "Flexible: may move within availability with approval.",
                    )
                    edit_pref_time = time_picker(
                        "Preferred time", t.preferredTime, f"edit_ttime_{key_prefix}_{t.taskId}"
                    )
                    edit_due_date = st.date_input(
                        "Due date", value=t.dueDate, key=f"edit_tdue_{key_prefix}_{t.taskId}"
                    )
                edit_task_save = st.form_submit_button("Save Changes")

            if edit_task_save:
                updated_task = Task(
                    taskId=t.taskId,
                    taskName=edit_task_name,
                    taskType=edit_task_type,
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
                owner.scheduler.editTask(updated_task)
                owner.save_to_json(DATA_FILE)
                st.success(f"Task '{edit_task_name}' updated!")
                st.rerun()


if "owner" not in st.session_state:
    loaded_owner = Owner.load_from_json(DATA_FILE)
    if loaded_owner:
        st.session_state.owner = loaded_owner

# ── Section 1: Owner Setup ────────────────────────────────────────────────────
st.subheader("Owner Setup")

owner_name = st.text_input("Your name", value="Jordan")
col1, col2 = st.columns(2)
with col1:
    start_time = time_picker("Available from", datetime.time(7, 0), "owner_start")
with col2:
    end_time = time_picker("Available until", datetime.time(19, 0), "owner_end")

if st.button("Create / Update Owner"):
    if "owner" not in st.session_state:
        st.session_state.owner = Owner(
            name=owner_name,
            startTime=start_time,
            endTime=end_time,
            preferences={},
        )
    else:
        o = st.session_state.owner
        o.name = owner_name
        o.startTime = start_time
        o.endTime = end_time
        o.scheduler.startTime = start_time
        o.scheduler.timeAvailable = o.availableMinutes
        o.scheduler.ownerName = owner_name
    st.session_state.owner.save_to_json(DATA_FILE)
    st.success(
        f"Owner saved successfully: {owner_name}\n\n"
        f"Availability: {format_time(start_time)} to {format_time(end_time)}\n\n"
        f"Total available time: "
        f"{format_duration(st.session_state.owner.getAvailableTime())}"
    )

if "owner" in st.session_state:
    o = st.session_state.owner
    st.caption(f"Active owner: **{o.name}** | {format_duration(o.getAvailableTime())} available")

# Guard: nothing below can run without an owner
if "owner" not in st.session_state:
    st.info("Fill in your name and time window, then click **Create / Update Owner** to continue.")
    st.stop()

owner: Owner = st.session_state.owner

# ── Section 2: Add a Pet ──────────────────────────────────────────────────────
st.divider()
st.subheader("Add a Pet")

with st.form("add_pet_form"):
    col1, col2 = st.columns(2)
    with col1:
        pet_name     = st.text_input("Pet name", value="Mochi")
        species      = st.selectbox("Species", ["Dog", "Cat", "Other"])
        breed        = st.text_input("Breed (optional)", value="")
        age          = st.number_input("Age (years)", min_value=0, max_value=30, value=2)
    with col2:
        energy_level = st.slider("Energy level", min_value=1, max_value=10, value=5)
        food_type    = st.text_input("Food type", value="Dry Kibble")
        medication   = st.text_input("Medication (or 'none')", value="none")
    pet_submitted = st.form_submit_button("Add Pet")

if pet_submitted:
    new_pet = Pet(
        name=pet_name,
        species=species,
        breed=breed,
        age=int(age),
        foodType=food_type,
        medication=medication,
        energyLevel=int(energy_level),
    )
    owner.addPet(new_pet)
    owner.save_to_json(DATA_FILE)
    st.success(f"Pet '{pet_name}' added!")

if owner.pets:
    st.markdown("**Your pets:**")
    species_options = ["Dog", "Cat", "Other"]
    for i, p in enumerate(owner.pets):
        with st.expander(f"🐾 {p.name}"):
            with st.form(key=f"edit_pet_form_{i}"):
                ecol1, ecol2 = st.columns(2)
                with ecol1:
                    edit_name    = st.text_input("Pet name", value=p.name, key=f"edit_name_{i}")
                    edit_species = st.selectbox(
                        "Species", species_options,
                        index=next(
                            (idx for idx, opt in enumerate(species_options) if opt.lower() == p.species.lower()),
                            2,
                        ),
                        key=f"edit_species_{i}",
                    )
                    edit_breed   = st.text_input("Breed (optional)", value=p.breed, key=f"edit_breed_{i}")
                    edit_age     = st.number_input(
                        "Age (years)", min_value=0, max_value=30, value=p.age, key=f"edit_age_{i}"
                    )
                with ecol2:
                    edit_energy  = st.slider(
                        "Energy level", min_value=1, max_value=10, value=p.energyLevel, key=f"edit_energy_{i}"
                    )
                    edit_food    = st.text_input("Food type", value=p.foodType, key=f"edit_food_{i}")
                    edit_med     = st.text_input(
                        "Medication (or 'none')", value=p.medication, key=f"edit_med_{i}"
                    )
                save_clicked = st.form_submit_button("Save Changes")

            if save_clicked:
                p.updatePet("name", edit_name)
                p.updatePet("species", edit_species)
                p.updatePet("breed", edit_breed)
                p.updatePet("age", int(edit_age))
                p.updatePet("energyLevel", int(edit_energy))
                p.updatePet("foodType", edit_food)
                p.updatePet("medication", edit_med)
                owner.save_to_json(DATA_FILE)
                st.success(f"Pet '{edit_name}' updated!")
                st.rerun()

            if st.button("Remove Pet", key=f"remove_pet_{i}"):
                owner.removePet(p)
                owner.save_to_json(DATA_FILE)
                st.success(f"Pet '{p.name}' removed.")
                st.rerun()
else:
    st.info("No pets yet. Add one above.")

# Guard: task form needs at least one pet for its selector
if not owner.pets:
    st.info("Add at least one pet above before scheduling tasks.")
    st.stop()

# ── Section 3: Schedule a Task ────────────────────────────────────────────────
st.divider()
st.subheader("Schedule a Task")

with st.form("add_task_form"):
    col1, col2 = st.columns(2)
    with col1:
        task_name    = st.text_input("Task Title", value="Morning Walk")
        task_type    = st.text_input("Task Type", value="Exercise")
        duration     = st.number_input("Duration (in minutes)", min_value=1, max_value=240, value=20)
        recurrence   = st.selectbox("Recurrence", ["None", "Daily", "Weekly"])
    with col2:
        priority_str = st.selectbox("Priority", ["high", "medium", "low"])
        flexibility_str = st.selectbox(
            "Flexibility", ["Fixed", "Preferred", "Flexible"], index=2,
            help="Fixed: must not move. Preferred: may move slightly with approval. "
                 "Flexible: may move within availability with approval.",
        )
        selected_pet = st.selectbox(
            "For which pet?",
            options=owner.pets,
            format_func=lambda p: p.name,
        )
        pref_time    = time_picker("Preferred time", datetime.time(8, 0), "add_task_time")
        due_date     = st.date_input("Due date", value=datetime.date.today())
    task_submitted = st.form_submit_button("Add Task")

if task_submitted:
    new_task = Task(
        taskName=task_name,
        taskType=task_type,
        durationMinutes=int(duration),
        priority=Priority(priority_str),
        pet=selected_pet,
        preferredTime=pref_time,
        flexibility=Flexibility(flexibility_str.lower()),
        recurrence=recurrence.lower(),
        dueDate=due_date,
    )
    owner.scheduler.addTask(new_task)
    owner.save_to_json(DATA_FILE)
    st.success(f"Task '{task_name}' added!")

# ── Queued tasks: sort, filter, and card display ──────────────────────────────
if owner.scheduler.tasks:
    st.markdown("**Queued tasks:**")

    fcol1, fcol2, fcol3 = st.columns(3)
    with fcol1:
        sort_choice = st.radio("Sort by", ["Time", "Priority", "None"], horizontal=True)
    with fcol2:
        status_choice = st.selectbox("Status", ["All", "Pending", "Completed"])
    with fcol3:
        pet_choice = st.selectbox("Pet", ["All"] + [p.name for p in owner.pets])

    completed_filter = None if status_choice == "All" else (status_choice == "Completed")
    pet_filter = None if pet_choice == "All" else pet_choice
    visible_tasks = owner.scheduler.filterTasks(completed=completed_filter, petName=pet_filter)

    if sort_choice == "Priority":
        ordered_ids = {t.taskId: i for i, t in enumerate(owner.scheduler.sortTasksByPriority())}
        visible_tasks = sorted(visible_tasks, key=lambda t: ordered_ids[t.taskId])
    elif sort_choice == "Time":
        visible_tasks = owner.scheduler.sort_by_time(visible_tasks)
    # "None" — leave visible_tasks in filterTasks()'s natural (insertion) order.

    if visible_tasks:
        for t in visible_tasks:
            render_task_card(t, owner, key_prefix="queued")
    else:
        st.info("No tasks match the current filters.")
else:
    st.info("No tasks queued yet.")

# ── Section 4: Generate Schedule ─────────────────────────────────────────────
st.divider()
st.subheader("Generate Schedule")

if not owner.scheduler.tasks:
    st.info("Add at least one task above before generating a schedule.")
else:
    if st.button("Generate Schedule"):
        owner.scheduler.generatePlan()
        scheduler = owner.scheduler

        st.markdown("**Daily Plan:**")

        if not scheduler.dailyPlan:
            st.info("No tasks were scheduled today.")
        else:
            total_minutes = sum(t.durationMinutes for t in scheduler.dailyPlan)
            st.success(
                f"{len(scheduler.dailyPlan)} task(s) scheduled — "
                f"{format_duration(total_minutes)} of {format_duration(scheduler.timeAvailable)} used."
            )

            tasks_by_pet: dict[str, list[Task]] = {}
            for t in scheduler.dailyPlan:
                tasks_by_pet.setdefault(t.pet.name, []).append(t)

            for pet_name_key, pet_tasks in tasks_by_pet.items():
                with st.container(border=True):
                    st.markdown(f"**🐾 {pet_name_key}**")
                    for t in sorted(pet_tasks, key=lambda task: task.preferredTime):
                        st.success(task_detail_line(t))

        if scheduler.unscheduledTasks:
            st.markdown("**Not scheduled** (insufficient time or outside window):")
            for t in scheduler.unscheduledTasks:
                st.warning(task_detail_line(t))

        conflicts_pairs = scheduler.getConflictWarnings()
        if conflicts_pairs:
            st.markdown("**Scheduling conflicts:**")
            for warning in conflicts_pairs:
                st.error(warning)
        else:
            st.success("No scheduling conflicts detected.")
