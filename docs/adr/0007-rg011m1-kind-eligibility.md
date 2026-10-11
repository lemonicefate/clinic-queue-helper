# Require RG011M1 KIND=1 for consultation displays

Status: accepted

## Context

`RG011M1.DBF` contains registrations used by the consultation queue and also
contains non-consultation front-desk transactions. Treating every active
registration as a waiting encounter can place a billing-only transaction in the
queue and can let a related VISIT observation create a misleading current-
patient card. The existing `TIME_KIND` field identifies the clinic session and
does not express this eligibility decision.

## Decision

Treat `RG011M1.KIND` as a required registration category. After trimming the
source value, only `KIND=1` is eligible for every consultation list and for the
read-only VISIT current-patient projection. `KIND=4`, blank, unknown, and other
unsupported values are excluded. The filter is applied per encounter, so a
non-consultation registration does not hide another consultation registration
with the same patient number.

The RG011M1 schema must contain `KIND`; a missing field fails closed with a
generic HIS source error. The raw `KIND` value remains part of the preserved
HIS snapshot so changes are detected by incremental polling. When a tracked
encounter changes away from `KIND=1`, its local queue row and VISIT association
are removed. If it later becomes `KIND=1` again, it is reintroduced at the tail
of its room-and-session sequence.

## Considered options

- Filtering only the waiting HTML would leave non-consultation records in
  completed, away, unmatched, or VISIT-derived views.
- Filtering by `NUM` would incorrectly remove a consultation registration when
  the patient has a separate front-desk transaction.
- Falling back to the old behavior when `KIND` is absent would display records
  whose consultation eligibility cannot be established.

## Consequences

The queue and VISIT monitor share one consultation eligibility boundary. A
source schema problem is visible as a generic synchronization error while the
application avoids exposing patient identities from ineligible records. A
temporary source value change can remove local ordering and presence state;
re-entry is deliberately treated as a new queue arrival.
