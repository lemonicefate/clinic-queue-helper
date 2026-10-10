# VISIT current-patient card controlled manual validation

This procedure is for a clinic-approved, read-only comparison of the default
VISIT current-patient card with the original HIS waiting-room display. It keeps
the evidence needed to validate source health and patient matching; the card is
not an official HIS-derived 看診中 state.

## Safety and setup

Use only a synthetic, de-identified, or HIS sandbox test encounter. Do not use
real patient data or real PHI. If the available HIS cannot provide a safe test
encounter, stop the procedure rather than substituting a real patient.

Run the application from the controlled test worktree with a read-only VISIT
source and application-owned SQLite and log paths outside the HIS data
directory. Keep the ordinary queue and the HIS source read-only from the
application's perspective. Before starting, confirm that each room has the
fixed `看診中` card above `候診中` and that it has no call, completion,
assignment, presence, overdue, or reorder control.

All raw captures must stay in a clinic-approved, access-controlled location
outside this repository and outside Git, GitHub, CI, the issue tracker, issue
comments, and pull requests. This includes real or synthetic DBF copies,
screenshots, application logs, API snapshots, and browser/network captures. The
repository may contain
only a redacted observation summary with dates, row counts, state labels, and
assumptions; never copy patient numbers, names, raw DBF bytes, screenshots, or
logs into it.

## Baseline capture

1. Record the test run identifier, local clinic date, selected physician code,
   selected `TIME_KIND`/session, and the original HIS waiting-room display's
   visible state using a redacted note.
2. Before calling anything, save the VISIT file's byte hash, byte length,
   record count, header/layout metadata, and a read-only baseline copy in the
   approved external capture location. Save any screenshot or API response
   there too.
3. Start or restart the test application. Confirm that rows already present at
   startup project eligible stable current state without replaying a new event
   or changing ordinary queue membership, order, presence, or overdue values.
4. Record the monitor API status and the rendered per-room/session status.
   Valid evidence labels are `NONE`, `UNKNOWN`, `AMBIGUOUS`, `STALE`, and
   `ERROR`; the card must never emit `ACTIVE`.

## Call and completion/deletion

1. In the original HIS waiting-room display, call exactly one safe test
   encounter. Do not call or complete a real patient for this procedure.
2. After the HIS display settles, capture the before/after VISIT metadata and
   the original-display result externally. Poll the application until the
   same complete VISIT record bytes have been read twice. Record the physical
   record locator, association reason, room, session, and monitor state in a
   redacted summary. A single stable candidate is evidence with `UNKNOWN`, not
   confirmation of consultation.
3. Complete the same test encounter in HIS. Capture the follow-up metadata and
   original-display result externally, including whether the VISIT row was
   logically deleted or changed in another way. After two stable reads, verify
   that deletion removes only that evidence and is not reported as `完成` or
   `ACTIVE` by the card.
4. Compare the ordinary queue before and after the call/completion sequence.
   Any queue, order, presence, overdue, room-filter, or HIS-source mutation is
   a failed safety result and must be recorded without attempting a local fix.

## Repeated calls and rapid switching

Repeat the call sequence with the same safe test encounter and then switch
quickly between two safe test encounters. For each transition, capture the
external VISIT metadata and original-display observation, then repeat the
two-identical-read check. Verify that repeated rows for one encounter are
coalesced with all physical locators retained, while different candidates for
one physician/session remain `AMBIGUOUS`. The monitor must not choose the
highest record number, newest timestamp, or latest non-deleted row as the
current patient.

## Reopening a called patient

Reopen the details of a previously called safe test encounter, once before
completion and once after completion where the HIS workflow permits it. Capture
any VISIT row or deletion-marker change externally and compare it with the
original HIS display. Treat a new row as observation evidence only; do not
promote it to a current-patient rule merely because it was created by opening a
detail page.

## Completed-detail-page noise

Use a safe test encounter that is already HIS-derived `完成`, then open its
completed detail page. If the HIS creates a VISIT row, verify that an exact
encounter association is excluded from the current-patient candidate list. The
diagnostic reason may be recorded in the redacted summary, but the raw row,
patient identity, screenshot, and log remain outside the repository and issue
tracker. A later encounter sharing the same patient number must be tested
separately and must not be suppressed by the earlier completion.

## Another physician or session

Repeat the baseline, call, and completion/deletion observations with a second
safe physician code and at least one different `TIME_KIND` (早診、午診、 or
晚診). Verify that each candidate appears only in the room whose shared
`CCDOC` filter matches and only under the browser's selected session. A blank
or unmatched physician remains in the read-only unmatched diagnostic area; it
is never assigned to a room. Use separate browser session selections to ensure
one workstation's session does not change another's view.

## Failure and recovery checks

Only if the clinic's test environment permits it safely, observe a temporary
read failure, a truncated source, and a source replacement using copies of the
synthetic/test VISIT file. Confirm `ERROR` on an initial failure and `STALE`
after a usable observation, with no fabricated `NONE` or patient row. Restore
the stable source and confirm recovery. Compare byte hashes before and after
each monitor poll; the application must not write, delete, pack, reindex, or
otherwise change the VISIT bytes.

## Assumptions and unresolved behavior

Record each observation as one of `observed`, `not observed`, or `unable to
test`. Keep these items as assumptions until repeated controlled comparisons
agree with the original HIS display:

- what exact HIS action creates a VISIT row and whether it is immediate;
- whether repeated calls, rapid switching, or reopening details create new
  rows, mutate old rows, or both;
- whether completion always uses a logical deletion marker or another change;
- whether one physician/session can have more than one valid candidate at a
  time;
- whether a VISIT record's timestamp or physical order has clinical meaning;
- how source truncation, replacement, sharing failures, and recovery appear;
- whether the original HIS display and VISIT source settle at different times.

Do not turn any of these assumptions into an `ACTIVE` inference rule from one
manual run or from synthetic tests alone. End the run by restoring the approved
synthetic source and preserving only the redacted summary in the worktree;
keep all raw patient data, DBF files, screenshots, logs, and snapshots in the
approved external location under the clinic's retention policy.
