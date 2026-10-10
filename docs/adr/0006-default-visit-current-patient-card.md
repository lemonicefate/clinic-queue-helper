# Default VISIT current-patient card and presentation-only suppression

Status: accepted

## Context

ADR 0005 established a read-only VISIT observation over `RG011M1_VISIT.DBF` while the source semantics were being validated. The controlled comparison is complete enough for the observation to be useful during normal queue work. Staff need the current-patient display in the room board, while HIS-derived visit state, local presence, and QueueStore order must remain authoritative for their existing responsibilities.

## Decision

The application always initializes and exposes one fixed `看診中卡片` slot per visible room and selected `TIME_KIND`. The VISIT filename remains configurable and defaults to `RG011M1_VISIT.DBF`; the legacy `visit_monitor_enabled` setting is accepted for configuration compatibility but has no effect.

The card is a read-only display projection. A complete startup baseline projects eligible stable current state without replaying an event. New or changed physical VISIT records still require two identical complete reads. `UNKNOWN`, `AMBIGUOUS`, `STALE`, `ERROR`, and `NONE` remain internal/API evidence states. Only one stable `UNKNOWN` candidate may suppress waiting cards; stale source state retains suppression after a valid candidate, while ambiguity, initial error, and no candidate do not suppress.

Suppression applies only to `候診中` entries whose normalized `NUM`, clinic date, room, and `TIME_KIND` match the unique candidate. NUM is used for this presentation rule only. The projection leaves QueueStore rows, `queue_position`, presence, ordering actions, completed state, unmatched entries, unconfirmed sessions, and HIS bytes unchanged. It exposes suppressed encounter keys beside the independent monitor projection and returns the same filtered board from GET and queue-changing endpoints.

The primary card shows only patient identity. Source health, association, ambiguity, deletion, unmatched candidates, and other diagnostics remain available in a collapsed read-only diagnostic area. The card is not an official HIS-derived `就診狀態`, does not call or complete patients, and does not write VISIT or RG data.

## Consequences

The board can temporarily omit a duplicate waiting card while preserving the encounter's underlying position. When valid evidence disappears without HIS completion, the card returns at that position; if HIS marks the encounter complete, normal reconciliation moves it to `完成`. A local away/present transition remains owned by QueueStore and the projection is recalculated over the resulting list.

ADR 0005 remains in the history as the decision that preceded validation. This ADR supersedes its optional and experimental presentation, while retaining its source-safety, identity, stability, and read-only constraints.

