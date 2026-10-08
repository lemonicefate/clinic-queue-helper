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

2. Build a usable, session-aware waiting queue for clinic staff.

3. Allow clinic staff to manage local presence, waiting order, and overdue state; derive room membership only from the HIS `CCDOC` value and the shared 一診／二診 filters.

4. Keep all application-owned state in a separate SQLite database.

5. Let HIS perform actual patient calling; this application does not call patients or set a current consultation.

6. NEVER modify HIS DBF/CDX/FPT files.

The MVP favors simplicity and practical clinic use over enterprise-grade architecture.

---

# 2. Primary Outcome

After launching the application on one Windows computer inside the clinic LAN:

- clinic staff can open the tool in a browser

- the server's system-local date is used with `TETDAY` to select today's HIS registrations automatically

- staff can select a clinic session: `TIME_KIND=1` 早診, `2` 午診, or `3` 晚診

- a browser starts on 早診 and remembers its own selection; session choice is not shared between browsers

- every visible patient list, including room queues, unmatched records, and completed patients, is filtered to the selected session; missing or unsupported session records appear under `診別未確認`

- records with a missing or unsupported `TIME_KIND` remain visible under `診別未確認`

- 一診 and 二診 queues are ordered independently for each clinic session

- staff can see patient name, queue number, status, and waiting order

- front desk, nurses, and physicians can:

  - mark patient present

  - mark patient temporarily away

  - reorder patients

  - move patients up/down

  - drag-and-drop patients

  - mark overdue

  - reorder within a room and session for list organization

- HIS performs actual calling; the application has no `下一位`, manual `叫號`, or `設為目前看診` action

- visit categories are limited to waiting, completed, and not checked in; available HIS fields do not establish a separate called or in-consultation state

- HIS completion/deletion state automatically reconciles with the queue

- shared queue changes normally appear across browsers within approximately 2 seconds; a browser with the session selector or a physician filter focused defers its page reload until focus leaves and any save finishes

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

- visible synchronization between clients: &lt;= 2 seconds under normal LAN conditions, except while a browser defers reload during protected interaction

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

| `OVER=F` | not completed; does not establish that consultation has started |

| `OVER=T` | completed |

| `TREAT=N` | not yet seen |

| `TREAT=B` | temporary saved record; does not establish that consultation has started |

| `TREAT=Y` | completed |

| `TREAT=C` | preregistered / not checked in |

| `TRDATE` | completion date |

| `TRTIME` | completion time |

| `TIME_KIND=1` | 早診 |

| `TIME_KIND=2` | 午診 |

| `TIME_KIND=3` | 晚診 |

| `GINO1` | queue-related raw identifier |

| `RELKEY` | encounter identity candidate |

| `SYS_2015` | DBF relationship / identity candidate |

Do not assume undocumented meanings beyond this table.

Preserve raw HIS values.

---

# 9. Today's Encounter Selection

The primary criterion for today's clinic queue is:

`TETDAY == local clinic date`

The local clinic date is the server computer's current system-local date. Polling re-evaluates today's date, including after the server crosses local midnight. No manual date picker is provided.

NOT:

`CCDATE == today`

because a patient may have preregistered before the actual visit date.

Exclude records where:

`_DELETED == True`

from active queues.

However, if an already tracked record later becomes deleted, reconcile it as described below.

After selecting today's encounters, the selected `TIME_KIND` determines the session-specific view and queue order. A missing or unsupported `TIME_KIND` is shown separately as `診別未確認` and is not included in one of the three session queues.

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

## Preregistered / not checked in

If:

`TREAT == C`

then:

`HIS_STATE = PREREGISTERED` (display label: `未報到`)

Initial application presence should default to:

`AWAY`

unless a persisted manual application state already exists.

---

## Waiting

If the encounter is active, is not completed, and does not have `TREAT=C`:

`HIS_STATE = WAITING`

Initial application presence defaults to:

`PRESENT`

This includes `TREAT=N`, temporary `TREAT=B` / `OVER=F`, and unknown active combinations. These temporary values do not indicate that a patient has been called or has entered consultation. A completed condition takes precedence over `TREAT=C`; otherwise `TREAT=C` takes precedence over `OVER=F`.

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

- waiting order within a room and `TIME_KIND`

- overdue flag

- shared 一診／二診 physician-code filters; room membership itself is derived only from HIS `CCDOC`

- new-patient highlight state

HIS performs actual patient calling. The local application does not own a called/current state and must not write queue actions to HIS.

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

`TREAT=C` is displayed as the HIS-derived visit category `未報到`. This is distinct from the local `AWAY` presence state, which staff may still change.

All users can change presence:

- front desk

- nurses

- physicians

There is no role-based authorization in MVP.

---

# 15. Visit Categories and Queue States

The HIS-derived visit categories shown to staff are:

- `WAITING` — includes active `TREAT=N`, `TREAT=B`, `OVER=F`, and unknown active combinations

- `PREREGISTERED` — displayed as `未報到` when `TREAT=C` applies and the encounter is not completed

- `COMPLETED` — when `OVER=T` or `TREAT=Y` applies

Deleted encounters remain internally `INVALIDATED` and are excluded from active lists. Completion takes precedence over preregistration. The available HIS fields do not identify `CALLED` or `IN_CONSULTATION`; do not synthesize or display those states.

HIS completion automatically transitions to COMPLETED. HIS deletion automatically transitions to INVALIDATED.

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

The system supports two clinic rooms:

- 一診 (`room_id=1`)

- 二診 (`room_id=2`)

Each room's physician-code filter is editable on the queue page, stored in the server's SQLite database, and shared by all workstations connected to that server. Each non-empty filter is unique across the two rooms. A blank filter means that room has no session and therefore has no queue. An encounter belongs to a room only when its HIS `CCDOC` matches that room's filter.

Each `(room, TIME_KIND)` pair has an independent local queue and one-based waiting order. A patient's local order in one session must not change the order in another session.

Encounters with an unknown `TIME_KIND` are visible in `診別未確認` and do not belong to one of the three session-specific queue orders.

Both rooms may simultaneously contain the same displayed queue number.

Example:

- 一診 #15

- 二診 #15

This is valid.

---

# 18. Shared CCDOC Room Filters

`CCDOC` is the sole source of room membership. Staff enter one physician account in each room's filter field on the queue page. The filters are persisted in the server-side SQLite database, so every workstation connected to that server sees and uses the same values. Save a field when it loses focus; save only the field that changed. The most recently accepted save is authoritative.

- Do not provide manual encounter-to-room assignment or a room-assignment override.
- Reject a non-empty `CCDOC` value if it is already selected for the other room.
- A blank filter means that room has no active session and no queue; it does not mean "show all doctors."
- If an encounter's `CCDOC` matches neither filter, keep it visible in a separate read-only `未納入一診／二診篩選` area. Do not add an assignment state or manual assignment action.
- If an existing encounter moves between matched and unmatched status after a filter changes, preserve its local presence and overdue values. When it enters a room's queue, append it to that room and session's tail; preserve unaffected queue order.
- For databases that do not yet have saved room filters, a legacy `doctor_room_map` may seed a room only when exactly one code maps to it. This seed is a migration aid only; afterward the SQLite filters are authoritative. Ambiguous legacy mappings leave that room blank for staff entry.
- Do not infer room membership from any other HIS field.

---

# 19. New Registration Behavior

When a new active encounter for today appears:

1. resolve room only by matching HIS `CCDOC` to the shared 一診／二診 filters; unmatched encounters remain read-only outside both room queues

2. determine initial presence from HIS state

3. if PRESENT, waiting, and matched to a room, append to that `(room, TIME_KIND)` queue tail

4. if AWAY and matched to a room, show in that room's "暫未到診" section; otherwise keep it in the read-only unmatched section

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

Both operations are scoped to the encounter's `(room, TIME_KIND)` queue. A move or drag must not change another session's order. Local ordering organizes the view only and does not change HIS's actual call order.

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

# 25. HIS-Owned Calling

HIS performs actual patient calling. The application is a read-only mirror of HIS visit data for call and consultation state.

Do not provide `下一位`, manual `叫號`, or `設為目前看診` controls or equivalent local API actions. The available `OVER` and `TREAT` fields do not distinguish called from in-consultation; the application displays only the visit categories defined in Section 15.

Presence, overdue state, and session-scoped ordering remain available for clinic staff. Room membership is derived only from `CCDOC` matching a shared 一診／二診 filter; there is no local room-assignment override or assignment control. These controls organize the local view and do not call patients or write to HIS.

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

3. preserve its application state for the current clinic day

Completed encounters with a recognized `TIME_KIND` appear in the selected session's collapsed completed section. Completed encounters with an unknown `TIME_KIND` remain under `診別未確認`.

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

- append to the new `(room, TIME_KIND)` queue tail

- apply NEW highlight

Do not attempt to merge the old and new HIS records.

---

# 29. UI Layout

Default desktop layout:

two room columns on one screen.

Show a clinic-session selector for 早診, 午診, and 晚診. A browser defaults to 早診 and remembers its selection independently of other browsers.

Example:

\`\`\`text

+--------------------------+ +--------------------------+

| 一診                      | | 二診                      |

| Selected session: 早診    | | Selected session: 早診    |

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

- 一診 only

- 二診 only

- selected-session view across every visible patient list

- a separate `診別未確認` group that remains visible regardless of the selected session

Use URL parameters or routes.

Examples:

`/`

`/?room=1`

`/?room=2`

`/?room=1&time_kind=2`

Optional:

`/?room=1&mode=doctor`

Room filter affects presentation only.

The browser's selected session is local to that browser and does not change other browsers' selections. Session filtering includes room queues, temporarily-away patients, unmatched read-only encounters, and completed patients. Room filters are shared across workstations on the server. `診別未確認` remains visible as a collapsed section with a count; during testing, show the section even when the count is zero.

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

  - 未報到

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

The section is filtered to the selected session. Completed encounters with unknown `TIME_KIND` stay in `診別未確認`.

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

- time\_kind (1, 2, 3, or null when missing/unsupported)

- room\_id

- presence\_status

- queue\_status

- queue\_position

- overdue

- new\_highlight\_until

- manually\_reordered\_at

- created\_at

- updated\_at

`queue_position` is scoped to `(room_id, time_kind)`; changing one session's order must not alter another session's order.

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

Preserve the raw HIS field alongside normalized `time_kind`. Migrate existing active rows by deriving `time_kind` from their saved raw HIS fields, clear legacy local call/current states, and normalize positions independently per room and recognized session. Unknown sessions remain outside the three session queues.

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

User actions should immediately update server state and then naturally synchronize other clients. A browser with the session selector or physician-code field focused defers its page reload until focus leaves and any save finishes; other browsers continue to receive updates normally.

When a full-page refresh occurs, restore expanded per-patient registration details for matching encounters in the same browser tab. New encounters remain collapsed. This state does not change the default collapsed state of the top-level `已完成` and `診別未確認` sections.

Queue data is shared across browsers, while each browser's selected `TIME_KIND` is a browser-local preference. The selected session must be applied consistently to the initial page and subsequent queue polling.

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

## CCDOC does not match either room filter

- show the encounter in `未納入一診／二診篩選`
- keep the encounter read-only and visible, with no manual room assignment control or state

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

- `CCDOC` not matching either configured room filter

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

- OVER=F maps to WAITING, not consultation

- OVER=T

- TREAT=B maps to WAITING, not consultation

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

- independent ordering for the same room across multiple `TIME_KIND` values

- drag/drop equivalent API reorder

- move up

- move down

- overdue

- completed

- deleted

- doctor change represented as delete + new encounter

- restart reconciliation

- Last Write Wins

## Session and browser view integration

Use the existing highest-level ASGI application seam with a real temporary SQLite queue store. Tests should assert external behavior through rendered pages and queue API responses, including:

- first visit defaults to 早診 and each browser can retain a different selected session

- every visible patient category is filtered to the selected session

- missing/unsupported `TIME_KIND` remains visible under `診別未確認`

- completed patients follow the same session filter

- room and session orders remain independent after a filter change and reorder operations

- no `下一位`, `叫號`, or `設為目前看診` control is exposed

Prefer assertions on user-visible list membership, order, labels, and available actions over assertions on internal helper structure.

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

- `CCDOC` matching neither room filter

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

- manual calling or manual current-consultation state in Clinic Queue Helper

- automatic session inference from clock time

- a distinct called or in-consultation label until HIS supplies a reliable field/value for it

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

9. Each 一診／二診 and `TIME_KIND` combination has an independent queue order.

10. Each room's shared `CCDOC` filter is saved in server-side SQLite and applies to all workstations.

11. Encounters matching neither room filter remain visible in a read-only unmatched area, with no manual assignment.

12. New registrations append to the selected `(room, TIME_KIND)` queue tail.

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

28. Single-room/doctor view still allows local queue-management actions, without call/current actions.

29. Completed section is collapsible.

30. Multiple browsers normally synchronize within approximately 2 seconds; a browser actively using the session selector or editing a physician filter refreshes after that interaction and any save complete.

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

41. The browser defaults to 早診 and remembers its session selection independently.

42. 早診, 午診, and 晚診 are filtered by `TIME_KIND` across every visible list.

43. Records with missing or unsupported `TIME_KIND` remain visible under `診別未確認`.

44. Visit classification uses the agreed precedence: completed, then `TREAT=C` as `未報到`, then other active states as waiting.

45. `TREAT=B` and `OVER=F` never imply that consultation has begun.

46. HIS owns actual calling; local next/call/current actions are absent.

47. `CCDOC` is the only room-membership source. Presence, overdue, and queue-order changes remain application-owned and do not write to HIS.

48. Completed records follow the same selected-session filter; unknown sessions stay under `診別未確認`.

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

---

# 46. Session-Aware Queues and CCDOC Room Filters

## Problem Statement

Staff currently see today's encounters without a clinic-session filter, so morning, afternoon, and evening visits are mixed in the same room order. The local tool also exposes call/current-patient controls even though HIS owns actual calling. The available HIS fields do not identify a distinct called or in-consultation state.

## Solution

Continue selecting the clinic day from the server's system-local date and HIS `TETDAY`. Add a browser-local clinic-session selection based on `TIME_KIND`: `1` 早診, `2` 午診, and `3` 晚診. Default to 早診, remember each browser's selection independently, filter all visible patient lists, and keep records with missing or unsupported `TIME_KIND` visible as `診別未確認`.

Give each `(room, TIME_KIND)` pair its own local candidate order. Derive room membership only from the physician account in HIS `CCDOC`, using one shared editable filter for 一診 and one for 二診. Keep unmatched encounters visible in a separate read-only area, and keep presence, overdue, and ordering controls for matched room queues. HIS performs actual calls. Display only the supported visit categories: 候診, 完成, and 未報到.

## User Stories

1. As front-desk staff, I want today's encounters selected automatically from the server's current local date and `TETDAY`, so that I do not have to enter a date before opening the queue.
2. As front-desk staff, I want to select 早診, so that I see only morning encounters in the ordinary patient lists.
3. As front-desk staff, I want to select 午診, so that I see only afternoon encounters in the ordinary patient lists.
4. As front-desk staff, I want to select 晚診, so that I see only evening encounters in the ordinary patient lists.
5. As a staff member opening a browser for the first time, I want it to start on 早診, so that the initial view has a deterministic session.
6. As a staff member, I want my browser to remember its selected session, so that refreshes do not unexpectedly change my view.
7. As staff using separate workstations, I want each browser to keep its own selected session, so that one workstation's selection does not change another's.
8. As clinic staff, I want the selected session applied to both-room and single-room views, so that the room filter and session filter work together.
9. As clinic staff, I want the session filter applied to waiting patients, so that another session's waiting encounters do not appear in my queue.
10. As clinic staff, I want the session filter applied to temporarily-away patients, so that all visible room lists follow the same session.
11. As clinic staff, I want unmatched encounters filtered by the selected session and kept visible in a read-only area, so that every HIS encounter remains visible without creating a manual room assignment state.
12. As clinic staff, I want completed encounters filtered by the selected session, so that the completed section does not mix sessions.
13. As clinic staff, I want encounters with a missing or unsupported `TIME_KIND` to remain visible as `診別未確認`, so that data issues do not silently hide patients.
14. As clinic staff, I want `診別未確認` records to remain separate from 早診, 午診, and 晚診, so that no uncertain encounter is silently assigned to a session.
15. As clinic staff, I want to see 候診, 完成, and 未報到 as the supported visit categories, so that the interface does not imply a state that HIS does not provide.
16. As clinic staff, I want temporary `TREAT=B` or `OVER=F` records treated as 候診, so that temporary data is not mislabeled as an active consultation.
17. As clinic staff, I want `TREAT=C` displayed as 未報到 unless a completion condition applies, so that preregistered encounters are not mistaken for completed visits.
18. As clinic staff, I want each room and session to have an independent one-based waiting order, so that one session's patients do not create gaps in another session's order.
19. As a nurse reordering patients, I want drag-and-drop and up/down controls scoped to the selected room and session, so that changes do not reorder a different session.
20. As clinic staff, I want a new present encounter appended to its own room-and-session order, so that other session orders remain unchanged.
21. As clinic staff, I want to enter each room's physician `CCDOC` filter on the page, so that HIS determines room membership consistently across all workstations.
22. As a nurse, I want to keep marking patients 已到診 or 暫未到診, so that physical presence can be managed independently of HIS calling.
23. As clinic staff, I want to keep marking 過號, so that local staff can organize follow-up without an automatic overdue algorithm.
24. As clinic staff, I want HIS to remain responsible for actual calling, so that the local display cannot call a patient out of HIS order.
25. As clinic staff, I want the local 下一位, 叫號, and 設為目前看診 controls removed, so that local actions cannot contradict HIS.
26. As clinic staff, I want the app not to show 已叫號 or 看診中 without a reliable HIS signal, so that the displayed state remains evidence-based.
27. As staff at another browser, I want queue data changes to synchronize while my selected session remains independent, and refreshes to wait while I use the selector or edit a physician filter, so that shared updates do not interrupt my work or overwrite my view preference.
28. As the clinic, I want the HIS files to remain read-only, so that this feature cannot alter the source system's data.

## Implementation Decisions

- Use the server computer's system-local date with `TETDAY`; provide no manual date picker.
- Use `TIME_KIND` as the session identity: `1` 早診, `2` 午診, `3` 晚診.
- Store the selected session as a browser-local preference, defaulting to 早診. Do not infer a session from the current clock in this release.
- Store the 一診 and 二診 physician filters in the server's SQLite database so all workstations share them. Save an edited field on blur; a blank value means that room has no session and no queue. Reject duplicate non-empty values. The latest accepted save wins, and HIS reconciliation reads the persisted filters inside its write transaction so a stale poll cannot restore an older mapping.
- Suppress page reloads while the clinic-session selector or a physician-filter field has focus. After focus leaves the control, apply one pending refresh. Do not reload while a physician-filter save is in flight. Preserve each encounter card's registration-details disclosure state in the current tab across full-page refreshes; new encounters start collapsed.
- Apply the selected session to every visible patient category and to the initial page and subsequent queue polling.
- Keep missing/unsupported `TIME_KIND` records visible in a separate `診別未確認` group across selected-session views.
- Classify completed records first when `OVER=T` or `TREAT=Y`; otherwise classify `TREAT=C` as 未報到; classify other active records, including `TREAT=B`, `OVER=F`, and unknown active combinations, as 候診. Deleted records remain invalidated and excluded from active lists.
- Keep HIS-derived visit category separate from local presence (`PRESENT` / `AWAY`). A manual presence decision remains a local override.
- Maintain independent local order per `(room, TIME_KIND)`. New-encounter insertion, moving back from temporarily away, up/down moves, and drag-and-drop must affect only that order.
- Derive room membership exclusively from `CCDOC` matching a room filter. Do not expose manual assignment or an assignment override. Keep records matching neither filter in an independent read-only area. When a filter change moves a waiting encounter into a room, append it to the destination queue tail and preserve unaffected ordering; retain its local presence and overdue state.
- Keep shared room filters and application-owned presence, overdue, and reorder state in SQLite. These operations do not write to HIS or control HIS call order.
- Remove local next, call, and set-current behavior. Do not display a separate called or in-consultation state because current HIS fields do not supply one.
- Persist normalized `TIME_KIND` with the encounter state while retaining the raw HIS value. Treat missing/unsupported values as unknown. Unknown `TIME_KIND` takes precedence over the unmatched area and appears only in `診別未確認`; the latter is collapsed by default and shows a count, including zero during testing. Completed unmatched encounters remain read-only in the unmatched area.
- Continue browser polling so shared queue changes appear within the existing synchronization target; session preference remains local to each browser. Polling must not interrupt interaction with the selector or physician filters.

## Testing Decisions

- A good test asserts user-visible membership, status, ordering, and available actions; it should not depend on private helper structure.
- Use one high-level ASGI integration seam with the real application and a temporary SQLite queue store. Exercise rendered page responses and queue API behavior through HTTP requests.
- Cover each `TIME_KIND`, browser-specific selections, all visible list categories, completed-list filtering, unknown-session visibility, shared room filters, filter validation, reconciliation, and the removed assignment/call controls through the ASGI seam.
- Verify focus and deferred-reload behavior with a browser acceptance check that changes shared state while the session selector or a physician filter is focused, then confirms one refresh after focus leaves and any save finishes. Do not add a separate browser automation framework solely for this check.
- Exercise room/session-specific ordering through CCDOC filters and reorder operations, then verify another session's order is unchanged.
- Verify the resolver precedence for deleted, completed, preregistered, temporary, waiting, and unknown active records.
- Prior art: existing pytest view coverage uses HTTPX ASGI transport and a seeded queue store; queue behavior and HIS state interpretation already have focused pytest coverage.
- Preserve existing DBF read-only verification and HIS polling/restart coverage.

## Out of Scope

- Manual date selection.
- Automatic session inference from time-of-day; this may be added later behind a future configuration if requested.
- App-initiated calls, 下一位, manual 叫號, or manual current-consultation state.
- A distinct 已叫號 or 看診中 category until HIS exposes a reliable field/value for it.
- Writing local queue, room, presence, overdue, or ordering state back to HIS.
- Automatic cross-room balancing or automatic queue optimization.

## Further Notes

- The existing polling flow already uses the server's system-local date; this revision makes the date basis explicit and adds the session dimension.
- `OVER=F` and `TREAT=B` mean temporary/not completed, not that consultation has begun. Their visit category is 候診.
- `TREAT=C` means 未報到 unless `OVER=T` or `TREAT=Y` establishes completion.
- Local order is for organizing the displayed candidate list. HIS continues to determine who is actually called.
- Published for implementation as [GitHub issue #7](https://github.com/lemonicefate/clinic-queue-helper/issues/7) with the `ready-for-agent` label.
- Shared CCDOC room-filter behavior supersedes the static mapping and manual assignment requirements in issue #7; its follow-up is tracked in [GitHub issue #11](https://github.com/lemonicefate/clinic-queue-helper/issues/11).
