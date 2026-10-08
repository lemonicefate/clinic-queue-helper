# Clinic Queue Context

Terms for describing a patient's clinic visit and the local waiting order.

## Language

**門診時段 (`TIME_KIND`)**:
A visit's clinic session category: `1` is 早診, `2` is 午診, and `3` is 晚診.

**醫師代碼 (`CCDOC`)**:
The HIS value identifying the physician for an encounter; it is the sole source of 一診／二診 membership. Each room's filter is shared across workstations through the server's SQLite database and saves when its field loses focus.

**病歷號碼**:
The patient identifier carried by a registration. To resolve a patient's name, match the registration's `NUM` value to the corresponding identifier in the patient master record.

**診間（一診／二診）**:
One of the clinic's two consultation rooms; an encounter belongs to the room whose selected physician code matches its HIS `CCDOC` value. A blank selected code means there is no clinic session and no queue is created for that room. A code matching neither room remains visible in the read-only unmatched area.

**未納入診間篩選掛號**:
An encounter whose HIS `CCDOC` matches neither the 一診 nor 二診 filter; it remains visible for review but cannot be manually assigned to a room.

**診別未確認**:
A visit whose `TIME_KIND` is missing or does not identify one of the three defined clinic sessions.

**候診序列**:
The ordered set of encounters waiting in one consultation room during one clinic session.

**候診中**:
An active encounter that HIS classifies as waiting. `TREAT=C` remains outside this list; other active, non-completed HIS states enter it through synchronization.

**就診狀態**:
The HIS-derived visit states are 候診, 完成, and 已約未到. They are separate from local presence; the available HIS data does not identify a separate 已叫號 or 看診中 state.

**暫存 HIS 紀錄**:
A record indicated by `TREAT=B` or `OVER=F`; it is not evidence that the patient has entered consultation.

**已約未到**:
A preregistered visit indicated by `TREAT=C` unless a completion condition applies. It enters 候診中 only when HIS synchronization changes it to another active, non-completed state.

**已掛暫離**:
A waiting encounter that clinic staff manually mark as temporarily away. This local presence state is not represented in HIS and is distinct from 已約未到.

**回候診**:
The front desk action that returns an 已掛暫離 encounter to 候診中 at the tail of the same room-and-session sequence.

**暫未到診**:
Avoid as a list category because it combines 已約未到 and 已掛暫離, which have different sources and transitions.
