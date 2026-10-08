# HIS-owned patient calling

Status: accepted

HIS performs actual patient calling; Clinic Queue Helper keeps shared `CCDOC` room filters and local presence, overdue, and display-order controls but has no manual room assignment, `下一位`, manual `叫號`, or `設為目前看診` behavior. The available HIS fields distinguish waiting, completed, and preregistered/not-checked-in records, but do not provide a reliable called or in-consultation state, so the app does not invent or display one.
