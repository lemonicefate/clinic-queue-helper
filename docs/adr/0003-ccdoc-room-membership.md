# CCDOC determines room membership

Status: accepted

The HIS `CCDOC` value is the sole source of 一診／二診 membership. Each room has one physician-code filter, stored in the server's SQLite database and shared by every workstation using that server. A filter saves on blur; the most recently accepted save is authoritative. Blank means the room has no active session and no queue. Duplicate non-empty codes are rejected.

Encounters whose `CCDOC` matches neither filter remain visible in a separate read-only area. Staff cannot manually assign them or override the HIS-derived room. This accepts that a bad or missing HIS physician value may leave an encounter outside both queues; allowing a local override would make room membership disagree across staff and could be lost or contradicted during HIS reconciliation. If a filter change moves a waiting encounter into a room, preserve presence and overdue state and append it to the destination room-and-session queue tail, leaving unaffected order intact.

The browser defers full-page refresh while the session selector or a physician-code field is focused, and until physician-code saves finish. This keeps polling from interrupting selection or editing. HIS reconciliation reads persisted filters inside its write transaction so a stale poll cannot restore an older mapping. Browser session choice remains local; room filters remain shared.

For an existing database without saved filters, a legacy `doctor_room_map` may seed a room only when exactly one code maps to it. Once saved, the SQLite filter values are authoritative.
