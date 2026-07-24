# PawPal+ Project Reflection

## 1. System Design

**a. Initial design**

- Briefly describe your initial UML design.
- What classes did you include, and what responsibilities did you assign to each?

My initial UML design includes four main classes: Owner, Pet, Task, and Scheduler. The Owner class is responsible for storing basic owner information, preferences, available time, and the pets that belong to the owner. The Pet class is responsible for storing pet information such as name, breed, food needs, medication notes, and other care details. The Task class represents one care activity that needs to be completed, such as feeding, walking, grooming, or medication, and stores details like duration, priority, task type, and completion status. The Scheduler class is responsible for organizing tasks into a daily plan based on priority, available time, and possible conflicts between tasks. Overall, the design separates information storage from scheduling logic so that each class has a clear responsibility.


**b. Design changes**

- Did your design change during implementation?
- If yes, describe at least one change and why you made it.

I did change my design during implementation. A change I made was adding a unique ID to the task case because editTask() method would not be able to locate the task to make changes. 

---

## 2. Scheduling Logic and Tradeoffs

**a. Constraints and priorities**

- What constraints does your scheduler consider (for example: time, priority, preferences)?
- How did you decide which constraints mattered most?

My scheduler consider's  the owner's available time, task duration, task priority, completion status, due date, and whether a taask fits inside the owner's care window. It also cheecks for overlapping taask times separately so the user can see possible scheduling conflicts. I decidedd that priority and available time maattered most because PawPal+ should make sure the most important pet care tasks get scheduled first.


**b. Tradeoffs**

- Describe one tradeoff your scheduler makes.
- Why is that tradeoff reasonable for this scenario?

One tradeoff my scheduler makes is that it detects scheduling conflicts but does not automatically fix them by moving tasks to a new time. This is a reasonable tradeoff beausce some pet care task, such as medication or feeding, maay need to happen at a specific time instead of being automatically shifted. 

---

## 3. AI Collaboration

**a. How you used AI**

- How did you use AI tools during this project (for example: design brainstorming, debugging, refactoring)?
- What kinds of prompts or questions were most helpful?

I used AI tool during this project for searching for potential bottleneck in code, refractoring, and part of design brainstorming. The prompts that were most helpful were to act as an AI engineer and being as detail with what I wanted the AI to do such as describing the function of a specific class. Another question that was helpful was explaining complicated code to me like a 5 year old. 

**b. Judgment and verification**

- Describe one moment where you did not accept an AI suggestion as-is.
- How did you evaluate or verify what the AI suggested?
I do not accept an AI suggestion when it was adding additional method that I did not want to my pawpal_system.py. I evaluated the method through checking whether was it wrote could be a valid and productive to this project. That determined whether it would still be in it or not.
---

## 4. Testing and Verification

**a. What you tested**

- What behaviors did you test?
- Why were these tests important?

The behaviors that I tested where sorting tasks by time and priority, recurring task, conflict detection, and generating tasks. These tests were important because they are the crucial backbone of this service. Sorting tasks needed to be tested to make sure the user can see what they want to see and not be confused by a behavior that is not their intent. Recurring task was also important to make sure it will repeat what the user wants and not be put in the wrong day. Generating plan is very imporatant so it can stay within the user's availibilty and prevent conflicts. 

**b. Confidence**

- How confident are you that your scheduler works correctly?
- What edge cases would you test next if you had more time?

I am fairly confidednt that my scheduler works correctly. Some edge cases I would test next if I had more time are testing duplicate task, making sure a task that goes into another day by midnight would not cause issues with timing with recurrence. Another test I would have done is test the explanation of the plan to make sure the user would understand what is being said. 


---

## 5. Reflection

**a. What went well**

- What part of this project are you most satisfied with?

The part of this project that I am most satisfied with was the four classes Task, Owner, Scheduler, Pet. Having to design the system was pretty interesting.

**b. What you would improve**

- If you had another iteration, what would you improve or redesign?

I would improve the methods to generate a plan and editing a task if I had another iteration to become more efficient. 

**c. Key takeaway**

- What is one important thing you learned about designing systems or working with AI on this project?

One thing I learned about designed systems is to have a diagram before coding. It is important ask AI to explain the code that it is writing become it can make silly mistakes.