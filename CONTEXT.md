# Clinic Queue Context

Terms for describing a patient's clinic visit and the local waiting order.

## Language

**門診時段 (`TIME_KIND`)**:
A visit's clinic session category: `1` is 早診, `2` is 午診, and `3` is 晚診.

**醫師代碼 (`CCDOC`)**:
The HIS value identifying the physician for an encounter; it is the sole source of 一診／二診 membership. Each room's filter is shared across workstations through the server's SQLite database and saves when its field loses focus.

**診間（一診／二診）**:
One of the clinic's two consultation rooms; an encounter belongs to the room whose selected physician code matches its HIS `CCDOC` value. A blank selected code means there is no clinic session and no queue is created for that room. A code matching neither room remains visible in the read-only unmatched area.

**未納入診間篩選掛號**:
An encounter whose HIS `CCDOC` matches neither the 一診 nor 二診 filter; it remains visible for review but cannot be manually assigned to a room.

**診別未確認**:
A visit whose `TIME_KIND` is missing or does not identify one of the three defined clinic sessions.

**候診序列**:
The ordered set of encounters waiting in one consultation room during one clinic session.

**就診狀態**:
The currently distinguishable visit states are 候診, 完成, and 未報到; the available HIS data does not identify a separate 已叫號 or 看診中 state.

**暫存 HIS 紀錄**:
A record indicated by `TREAT=B` or `OVER=F`; it is not evidence that the patient has entered consultation.

**未報到**:
A preregistered visit that has not been checked in, indicated by `TREAT=C` unless a completion condition applies.
