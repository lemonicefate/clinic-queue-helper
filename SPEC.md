# Clinic Queue Helper MVP — SPEC

## 1. Product Positioning

Clinic Queue Helper is a small internal clinic utility.

It is NOT:

- an HIS replacement

- a medical record system

- a billing system

- an NHI card system

- a patient-facing check-in system

- a cloud service

Its job is intentionally narrow:

1. Read today's registration / visit information from the existing Visual FoxPro DBF files.

2. Build a usable real-world waiting queue for clinic staff.

3. Allow front desk staff, nurses, and physicians to adjust queue state manually.

4. Keep all application-owned state in a separate SQLite database.

5. NEVER modify HIS DBF/CDX/FPT files.

The MVP favors simplicity and practical clinic use over enterprise-grade architecture.

---

# 2. Primary Outcome

After launching the application on one Windows computer inside the clinic LAN:

- clinic staff can open the tool in a browser

- today's HIS registrations appear automatically

- patients are grouped into Room 1 / Room 2 queues

- staff can see patient name, queue number, status, and waiting order

- front desk, nurses, and physicians can:

  - mark patient present

  - mark patient temporarily away

  - reorder patients

  - move patients up/down

  - drag-and-drop patients

  - mark overdue

  - call/set current patient where applicable

- HIS completion/deletion state automatically reconciles with the queue

- multiple browsers synchronize within approximately 2 seconds

- application state survives restart

- HIS source files remain strictly read-only

---

# 3. Mandatory Implementation Workflow

Implementation MUST follow this exact sequence.

## Step 1 — Convert SPEC to tickets

Before modifying application code, read and follow:

`C:\Users\lemon\.codex\skills\to-tickets\[SKILL.md](http://SKILL.md)`

Use `$to-tickets` to break this SPEC into implementation tickets.

Do NOT begin implementation until the ticket breakdown exists.

Follow the skill's conventions for:

- ticket structure

- dependencies

- ordering

- acceptance criteria

- ticket storage/output

The ticket set must cover the complete MVP.

---

## Step 2 — Implement tickets

After tickets exist, read and follow:

`C:\Users\lemon\.codex\skills\implement\[SKILL.md](http://SKILL.md)`

Use `$implement` to execute the ticket plan.

Do not substitute another implementation workflow unless the referenced skill explicitly delegates it.

---

## Step 3 — TDD

Required functionality must be implemented using TDD.

For each meaningful behavior:

1. RED

   - add or update a failing test demonstrating the required behavior

2. GREEN

   - implement the smallest code necessary to make the test pass

3. REFACTOR

   - clean up without changing behavior

Do not write superficial tests after implementation only to claim TDD.

Do not remove, skip, weaken, or rewrite valid failing tests merely to obtain a green test suite.

---

# 4. Hard Safety Invariant

## HIS files are read-only

This is the strongest invariant in the entire project.

The application MUST NEVER:

- open HIS DBF files with write access

- open HIS CDX files with write access

- open HIS FPT files with write access

- UPDATE DBF records

- INSERT into HIS DBFs

- DELETE DBF records

- PACK DBFs

- REINDEX DBFs

- rename HIS files

- move HIS files

- delete HIS files

- create files inside the HIS DATA directory

- obtain an exclusive lock on HIS files intentionally

All HIS access must be read-only.

Prefer a centralized DBF read-only adapter so HIS file access cannot accidentally bypass this rule.

Allowed conceptual access:

[`FileAccess.Read`](http://FileAccess.Read)

with sharing compatible with the existing HIS process.

Application-owned SQLite files and logs may be written normally outside the HIS directory.

---

# 5. Technology Stack

Use:

- Python 3

- FastAPI

- SQLite

- Jinja2/server-rendered HTML where useful

- vanilla JavaScript

- standard CSS

- pytest

- browser-based UI

Do NOT use:

- React

- Vue

- Angular

- Tauri

- Electron

- Redis

- Docker as a requirement

- Node build pipelines

- WebSocket unless clearly simpler than polling

- external cloud services

Browser synchronization may use simple HTTP polling.

Target:

- HIS polling: approximately 500 ms to 1 second

- browser queue refresh: approximately 1 second

- visible synchronization between clients: &lt;= 2 seconds under normal LAN conditions

---

# 6. Deployment Model

One Windows machine runs the FastAPI server.

Other clinic PCs access it over LAN.

Example:

[`http://192.168.x.x:8000`](http://192.168.x.x:8000`)

The application must bind to a configurable LAN interface.

Provide a simple Windows startup script.

Preferred files:

- `run.ps1`

- `config.example.json`

- [`README.md`](http://README.md)

`run.ps1` should make normal startup easy.

---

# 7. HIS Source

Default HIS folder:

`\\192.168.1.199\D\WM003\DATA`

The path MUST be configurable.

Primary source:

`RG011M1.DBF`

Patient lookup source:

`PD001M1.DBF`

`RG011M1_VISIT.DBF` is NOT required for the MVP critical path.

It may be ignored unless implementation discovers a clearly useful read-only purpose without increasing complexity.

---

# 8. RG011M1 Known Semantics

Current working interpretation:

| Field | Meaning |

|---|---|

| `_RECNO` | physical RG011M1 record number / locator |

| `_DELETED` | False = valid registration; True = deleted/invalid registration |

| `NUM` | 6-digit patient chart number |

| `CCDATE` | registration event date |

| `CCTIME` | registration event time |

| `TETDAY` | scheduled visit date |

| `CCDOC` | scheduled/current physician |

| `CRTUSER` | registration/operator account |

| `USID` | registration/operator account |

| `OVER` blank | not completed |

| `OVER=F` | temporary save/in-progress-like state |

| `OVER=T` | completed |

| `TREAT=N` | not yet seen |

| `TREAT=B` | temporary save/in-progress-like state |

| `TREAT=Y` | completed |

| `TREAT=C` | preregistered / likely not yet checked in |

| `TRDATE` | completion date |

| `TRTIME` | completion time |

| `TIME_KIND=1` | morning |

| `TIME_KIND=2` | afternoon |

| `TIME_KIND=3` | evening |

| `GINO1` | queue-related raw identifier |

| `RELKEY` | encounter identity candidate |

| `SYS_2015` | DBF relationship / identity candidate |

Do not assume undocumented meanings beyond this table.

Preserve raw HIS values.

---

# 9. Today's Encounter Selection

The primary criterion for today's clinic queue is:

`TETDAY == local clinic date`

NOT:

`CCDATE == today`

because a patient may have preregistered before the actual visit date.

Exclude records where:

`_DELETED == True`

from active queues.

However, if an already tracked record later becomes deleted, reconcile it as described below.

---

# 10. Encounter Identity

Do NOT treat `_RECNO` as a permanent business identifier.

`_RECNO` may be stored as a useful physical locator.

Implement encounter identity behind one dedicated resolver function/class.

Recommended MVP precedence:

1. non-empty `RELKEY`

2. otherwise a compound identity containing `SYS_2015`

3. otherwise a deterministic compound fallback based on available encounter fields and `_RECNO`

Example conceptual fallback:

`SYS_2015 + TETDAY + NUM + GINO1`

If required:

`_RECNO + TETDAY + NUM + CCDATE + CCTIME`

The exact resolver is an implementation detail, but it MUST be isolated so it can be replaced later.

Do not block MVP completion on fully reverse-engineering RELKEY or SYS_2015.

---

# 11. Patient Name Lookup

`RG011M1.NUM` maps to the patient master DBF:

`PD001M1.DBF`

Use PD001M1 only as a read-only lookup source.

The patient-number and patient-name field names should be configurable if necessary.

Do NOT copy unnecessary patient information into SQLite.

Do NOT ingest:

- national ID

- birthday

- address

- phone

- unrelated medical information

unless later explicitly required.

If patient name lookup fails:

- do not block the queue

- display chart number

- log a warning

---

# 12. HIS State Resolver

Raw HIS fields must remain available.

Create one dedicated resolver that converts HIS state into an application interpretation.

Use the following default precedence.

## Invalid

If:

`_DELETED == True`

then:

`HIS_STATE = INVALID`

---

## Completed

If either:

`OVER == T`

OR

`TREAT == Y`

then:

`HIS_STATE = COMPLETED`

---

## In progress / temporarily saved

If either:

`OVER == F`

OR

`TREAT == B`

then:

`HIS_STATE = IN_PROGRESS`

---

## Preregistered / likely not physically present

If:

`TREAT == C`

then:

`HIS_STATE = PREREGISTERED`

Initial application presence should default to:

`AWAY`

unless a persisted manual application state already exists.

---

## Waiting

If the encounter is active and:

`TREAT == N`

with no completed condition:

`HIS_STATE = WAITING`

Initial application presence defaults to:

`PRESENT`

---

## Unknown combinations

Unknown combinations must NOT crash or hide the patient.

Default to:

- active

- PRESENT

- WAITING

and preserve/display raw `TREAT` and `OVER` in details/debug information.

Log the unknown combination.

---

# 13. Manual App State Overrides

HIS state and application state are separate concepts.

HIS remains source-of-truth for:

- registration existence

- patient chart number

- scheduled doctor

- visit date

- queue number source

- deleted state

- completion state

The queue application owns:

- physical presence

- waiting order

- overdue flag

- room assignment override where applicable

- called/current state

- new-patient highlight state

A manual presence decision must NOT be continuously overwritten by ordinary HIS polling.

Example:

HIS:

`TREAT=C`

Application:

`PRESENT`

If staff manually marks the patient present, keep PRESENT until:

- the encounter is completed

- the encounter is deleted

- staff manually changes it again

---

# 14. Application Presence States

Minimum states:

- `PRESENT`

- `AWAY`

UI wording:

- 已到診

- 暫未到診

All users can change presence:

- front desk

- nurses

- physicians

There is no role-based authorization in MVP.

---

# 15. Queue States

Minimum useful queue states:

- `WAITING`

- `CALLED`

- `IN_CONSULTATION`

- `COMPLETED`

- `INVALIDATED`

Do not overengineer this state machine.

HIS `IN_PROGRESS` may automatically promote a tracked encounter to `IN_CONSULTATION`.

HIS completion automatically transitions to COMPLETED.

HIS deletion automatically transitions to INVALIDATED.

---

# 16. Queue Number

Keep the complete raw value:

`GINO1`

as:

`his_gino1_raw`

Current working assumption:

the last four characters represent the clinic/room queue number.

Example:

`1510070053`

=&gt; display queue number:

`53`

Implement parsing behind one small function.

If GINO1 cannot be parsed:

- preserve raw GINO1

- fall back to another simple visible identifier such as chart number

- do not crash

Do not attempt to fully reverse-engineer the leading GINO1 date format in MVP.

---

# 17. Rooms

The system supports:

- Room 1

- Room 2

Each room has an independent queue.

Both rooms may simultaneously contain the same displayed queue number.

Example:

- Room 1 #15

- Room 2 #15

This is valid.

---

# 18. Doctor-to-Room Mapping

Room assignment should be configurable.

Use something simple such as `config.json`.

Example concept:

\`\`\`json

{

  "doctor_room_map": {

    "DOCTOR\_CODE\_A": 1,

    "DOCTOR\_CODE\_B": 2

  }

}

\`\`\`

When the physician mapping exists:

- automatically place new encounter in the corresponding room queue

When no mapping exists:

- place the encounter in an `Unassigned` area

- clearly display it

- allow staff to manually move it into Room 1 or Room 2

- do not crash

Do not block MVP on discovering another HIS room field.

---

# 19. New Registration Behavior

When a new active encounter for today appears:

1. resolve room from doctor mapping

2. determine initial presence from HIS state

3. if PRESENT and waiting, append to room queue tail

4. if AWAY, show in that room's "暫未到診" section

5. apply NEW highlight

New encounters should NOT automatically insert ahead of existing waiting patients.

---

# 20. New Encounter Highlight

A newly discovered encounter receives a visible highlight.

Default duration:

`300 seconds`

This value must be configurable.

Highlight ends when either:

1. configured duration expires

2. staff manually changes its queue position

No separate acknowledgement workflow is required.

Use a visually obvious but not disruptive highlight.

---

# 21. Temporarily Away Workflow

Default:

registration is interpreted from HIS state.

Staff may manually mark any active encounter:

`暫未到診`

An AWAY patient:

- remains visible

- does not occupy the active waiting-order sequence

- stays inside the corresponding room's away section

When the patient returns:

staff selects:

`已到診`

Then:

- put patient into the active queue

- staff may choose/reorder the exact position manually

No automatic "wait three patients" rule.

---

# 22. Overdue Behavior

Overdue status is manual.

Staff may mark/unmark:

`過號`

Do NOT implement an automatic "wait three patients" algorithm.

An overdue patient may be dragged or moved to any position.

---

# 23. Reordering

All user types may reorder:

- front desk

- nurses

- physicians

Support BOTH:

- drag-and-drop

- up/down controls

Manual reorder clears NEW highlight for that encounter.

---

# 24. Concurrent Editing

Use:

`Last Write Wins`

Do not add row locks.

Do not add edit reservations.

Do not add "user X is editing" UI.

The server's accepted operation order is authoritative.

Do not depend on client wall-clock timestamps to determine winning writes.

SQLite updates should be transactional.

---

# 25. Call / Current Patient

All clinic users may use queue controls.

The tool may provide simple actions such as:

- 叫號

- 設為目前看診

- 下一位

Keep behavior simple.

Do not write any such action back into HIS.

If HIS state later indicates IN_PROGRESS, reconcile accordingly.

A physician opening the tool in single-room mode can still use queue actions.

"Doctor mode" is a presentation/filter mode, NOT a permission boundary.

---

# 26. Completed Encounter

When HIS changes an encounter to completed:

`OVER=T`

or:

`TREAT=Y`

the application should automatically:

1. remove it from the active waiting queue

2. move it to a collapsible "已完成" section

3. preserve its application state for the current clinic session/day

No manual completion is required.

---

# 27. Deleted Encounter

If a tracked record becomes:

`_DELETED=True`

the application automatically:

1. removes it from active queue

2. marks it INVALIDATED

3. does not require confirmation

---

# 28. Doctor Change

Current observed HIS behavior:

changing physician deletes/invalidates the previous registration and creates a new registration with new:

- CCDOC

- GINO1

Therefore:

- old deleted encounter becomes INVALIDATED

- new encounter is treated as a new registration

- resolve its new room

- append to new room queue tail

- apply NEW highlight

Do not attempt to merge the old and new HIS records.

---

# 29. UI Layout

Default desktop layout:

two room columns on one screen.

Example:

\`\`\`text

+--------------------------+ +--------------------------+

| Room 1                   | | Room 2                   |

| Current: #12 Name        | | Current: #08 Name        |

|                          | |                          |

| 1  #13 Name              | | 1  #09 Name              |

| 2  #14 Name [overdue]    | | 2  #11 Name              |

| 3  #16 Name              | | 3  #15 Name              |

|                          | |                          |

| Temporarily Away         | | Temporarily Away         |

+--------------------------+ +--------------------------+

\`\`\`

Provide:

- both-room view

- Room 1 only

- Room 2 only

Use URL parameters or routes.

Examples:

`/`

`/?room=1`

`/?room=2`

Optional:

`/?room=1&mode=doctor`

Room filter affects presentation only.

---

# 30. Patient Card

Default visible information should be minimal.

Show:

- queue number

- full patient name

- badges such as:

  - NEW

  - 過號

  - 暫未到診

  - 看診中

Example:

`#18 王小明 [過號]`

Detailed/secondary display may include:

- chart number

- doctor

- registration time

- raw HIS status if needed

Do not clutter the main queue card.

---

# 31. Name Display

This is an internal staff tool.

Display full patient name.

No masking is required in MVP.

---

# 32. Completed Section

Completed encounters remain visible for the current clinic day in a collapsed section.

Default:

`已完成 (12)`

Users can expand it if needed.

Do not keep completed patients mixed with active waiting patients.

---

# 33. Persistence

Use SQLite for application-owned state.

Suggested minimum tables:

## encounter\_state

Possible fields:

- encounter\_key

- his\_recno

- patient\_no

- room\_id

- presence\_status

- queue\_status

- queue\_position

- overdue

- new\_highlight\_until

- manually\_reordered\_at

- current\_flag

- created\_at

- updated\_at

## action\_log

Possible fields:

- id

- timestamp

- encounter\_key

- action

- old\_value

- new\_value

- client\_label or request source if easily available

Keep schema small.

Do not mirror entire HIS records into SQLite.

---

# 34. Restart Reconciliation

After application restart:

1. load SQLite state

2. read today's HIS encounters

3. match encounters using encounter identity resolver

4. preserve application-owned state where encounter still exists

5. add new HIS encounters

6. complete encounters HIS says are complete

7. invalidate encounters HIS says are deleted

8. remove stale active queue entries that no longer belong to today

Restart must not destroy manually configured queue order for still-valid encounters.

---

# 35. DBF Polling Strategy

Do NOT read the entire ~27 MB RG011M1 every 500 ms.

Use a lightweight strategy.

## Startup

Scan RG011M1 once.

Find today's encounters using:

`TETDAY == today`

Store their `_RECNO` locators.

---

## Continuous polling

Approximately every 500-1000 ms:

1. read DBF header

2. detect appended record count

3. inspect newly appended records

4. directly reread already tracked today's record offsets

5. compare tracked fields

6. reconcile changed rows

Since DBF records have fixed offsets, use direct seek where practical.

---

## Periodic recovery scan

Approximately every 30-60 seconds:

perform a full read-only scan for today's TETDAY.

Purpose:

- catch unusual changes

- catch records not appended normally

- self-heal after temporary errors

This is not a safety-critical process.

Keep implementation simple.

---

# 36. Browser Synchronization

Use ordinary polling.

Example:

client fetches queue state every 1 second.

Do not require WebSocket.

User actions should immediately update server state and then naturally synchronize other clients.

Target normal synchronization:

&lt;= 2 seconds

---

# 37. Configuration

Use a simple configuration file such as:

`config.json`

Minimum configurable fields:

\`\`\`json

{

  "his_data_path": "\\\\\\\\192.168.1.199\\\\D\\\\WM003\\\\DATA",

  "rg011m1_filename": "RG011M1.DBF",

  "pd001m1_filename": "PD001M1.DBF",

  "poll_interval_ms": 500,

  "browser_refresh_ms": 1000,

  "new_highlight_seconds": 300,

  "room_count": 2,

  "doctor_room_map": {},

  "patient_number_field": "NUM",

  "patient_name_field": ""

}

\`\`\`

If patient_name_field is empty or invalid:

- attempt only a conservative/simple discovery if obvious

- otherwise show patient number

- do not block startup

Provide `config.example.json`.

Do not commit environment-specific secrets.

There should not normally be any secrets in this project.

---

# 38. Error Handling

The tool must remain useful under ordinary clinic failures.

## HIS DBF temporarily unavailable

Do NOT crash the web application.

Instead:

- keep last known queue state

- show a visible banner:

  - `HIS 資料暫時無法同步`

- retry automatically

- clear banner when recovered

---

## Single DBF record parse error

- log warning

- skip problematic record

- continue processing others

---

## PD001M1 unavailable

- continue queue operation

- display patient chart number instead of name

- show warning

---

## Unknown doctor-room mapping

- move encounter to Unassigned

- allow manual assignment

---

## Unknown HIS status

- preserve raw values

- default to active WAITING/PRESENT

- log the raw combination

---

## SQLite failure

- report clearly in server log/UI

- never fall back to writing HIS files

- never attempt to use HIS DBFs as storage

---

## Port unavailable

Fail startup with a clear actionable error showing:

- requested port

- that another application may already be using it

---

## Unexpected server exception

- log stack trace to local application logs

- avoid killing the polling loop if recovery is possible

- preserve existing SQLite state

---

# 39. Logging

Keep logging practical.

Provide local application log, for example:

`logs/app.log`

Log:

- startup

- DBF connection/read failures

- recovery

- unknown HIS state combinations

- unknown doctor mapping

- queue changes

- manual presence changes

- overdue changes

- encounter completion

- encounter invalidation

Do not build a formal compliance audit system.

---

# 40. Testing Requirements

Use pytest.

Testing must prioritize the logic that could accidentally affect HIS safety or queue behavior.

Required coverage areas:

## DBF read-only adapter

Verify:

- DBF is opened read-only

- parser reads required field types

- record offsets work

- deleted record marker is detected

- no write operation exists in the HIS adapter

---

## HIS state resolver

Test at least:

- OVER blank + TREAT=N

- OVER=F

- OVER=T

- TREAT=B

- TREAT=Y

- TREAT=C

- `_DELETED=True`

- unknown combination

---

## Encounter identity

Test:

- RELKEY path

- SYS\_2015 fallback

- deterministic final fallback

---

## Queue behavior

Test:

- append new encounter

- AWAY does not occupy active queue position

- mark PRESENT

- reorder

- drag/drop equivalent API reorder

- move up

- move down

- overdue

- completed

- deleted

- doctor change represented as delete + new encounter

- restart reconciliation

- Last Write Wins

---

## Highlight behavior

Test:

- new encounter gets highlight

- highlight expires after configured interval

- manual reorder removes highlight immediately

---

## Error behavior

Test:

- DBF unavailable

- patient lookup unavailable

- unknown doctor

- unknown HIS status

---

# 41. Read-Only Verification

Development verification must explicitly demonstrate that polling does not modify HIS source files.

At minimum:

1. run DBF polling against test/fixture DBF files

2. record file hash before

3. run reads/polling

4. record file hash after

5. hashes must match

Also inspect HIS-access source code to confirm there are no write modes such as:

- `wb`

- `ab`

- `r+b`

- write/update DBF APIs

Do not modify real HIS DBFs merely for a test.

---

# 42. Out of Scope

The following are explicitly OUT OF SCOPE for MVP:

- authentication

- login

- RBAC

- account management

- cloud deployment

- internet-facing deployment

- mobile application

- patient-facing UI

- QR check-in

- barcode check-in

- NHI card reader

- billing

- prescriptions

- diagnosis entry

- NHI submission

- HIS DBF writes

- CDX writes

- FPT writes

- HIS UI automation

- AHK

- line\_bot2 integration

- waiting-room TV integration

- automatic three-patient overdue algorithm

- automatic queue optimization

- automatic cross-room balancing

- formal permissions

- enterprise audit framework

- Redis

- message queues

- microservices

- Docker requirement

- production installer

- auto updater

- analytics dashboard

- reports

- cloud backup

- CI/CD requirement

- performance benchmarking

- unnecessary abstractions

Do not add these "for future proofing."

---

# 43. UX Principle

This is a clinic utility.

Optimize for:

- large readable information

- minimal clicks

- immediate visual feedback

- staff use during busy clinic sessions

- mouse/touch-friendly controls

- low cognitive load

Do not optimize for visual novelty.

---

# 44. Definition of Done

The MVP is complete when all of the following are true.

1. Application launches on Windows.

2. Application reads RG011M1 read-only.

3. Today's encounters are selected using TETDAY.

4. Valid new encounters appear automatically.

5. `_DELETED=True` encounters do not remain active.

6. Completed encounters move automatically to Completed.

7. Patient names are shown when PD001M1 lookup is configured/available.

8. Failure to resolve a name does not block the queue.

9. Room 1 and Room 2 have independent queues.

10. Doctor-to-room mapping works from config.

11. Unknown doctors appear in Unassigned.

12. New registrations append to the room queue tail.

13. New registrations receive temporary highlight.

14. Highlight expires automatically.

15. Manual reorder removes highlight.

16. All clinic users can mark PRESENT/AWAY.

17. All clinic users can reorder.

18. Drag-and-drop works.

19. Up/down controls work.

20. Staff can manually mark overdue.

21. No automatic "wait three patients" algorithm exists.

22. Last Write Wins concurrency works.

23. Queue state persists in SQLite.

24. Restart reconciliation preserves valid manual queue state.

25. Both-room browser view works.

26. `?room=1` view works.

27. `?room=2` view works.

28. Single-room/doctor view still allows queue actions.

29. Completed section is collapsible.

30. Multiple browsers synchronize within approximately 2 seconds.

31. Temporary DBF read failure does not crash the application.

32. Application displays stale/sync warning during HIS read failure.

33. Tests pass.

34. Required behaviors were implemented using TDD.

35. HIS read-only verification passes.

36. No required MVP behavior is left as TODO.

37. README explains setup, configuration, startup, and URLs.

38. `config.example.json` exists.

39. `run.ps1` exists.

40. Implementation was first decomposed using `$to-tickets`, then executed using `$implement`.

---

# 45. Implementation Philosophy

When this specification leaves a minor implementation detail unspecified:

choose the smallest reasonable solution.

Do NOT stop to ask for clarification unless there is a genuine blocker that makes implementation impossible.

Do NOT expand product scope.

Do NOT introduce infrastructure merely because it would be appropriate for a larger production system.

Prefer:

simple &gt; clever

working &gt; generalized

readable &gt; abstract

clinic utility &gt; platform