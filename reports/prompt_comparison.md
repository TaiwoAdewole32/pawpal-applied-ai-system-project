# PawPal Sentinel: Baseline vs Specialized Prompt Comparison

Mode: fixture

Baseline prompt: `Review this pet-care schedule and improve it.`

Specialized prompt version: `pawpal-repair-v2-few-shot`

Scenarios compared: medication_conflict_with_flexible_walk, two_fixed_tasks_overlap, flexible_walk_outside_availability, repair_creates_new_conflict, capacity_exceeds_availability_window

| Metric | Baseline | Specialized |
|---|---|---|
| Valid structured outputs | 4/5 | 5/5 |
| Fixed tasks preserved | 3/5 | 5/5 |
| Unsafe proposals | 5/5 | 0/5 |
| Unknown task IDs | 0/5 | 0/5 |
| Conflict-free accepted plans | 0/5 | 5/5 |
