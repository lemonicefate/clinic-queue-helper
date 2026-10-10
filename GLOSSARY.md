# Clinic Queue Helper

This glossary defines the additional language used by the default read-only HIS current-patient card. It supplements `CONTEXT.md` without changing the existing queue vocabulary or official HIS-derived visit states.

## HIS observation

**HIS 目前病人觀測**:
A read-only observation of HIS records that may indicate which patient a physician is currently seeing. It is separate from HIS-derived visit states and local presence, and does not change the queue or declare an application-owned consultation state.
_Avoid_: 把觀測結果稱為應用程式管理的正式就診狀態

**目前病人候選**:
A patient identity suggested by one or more HIS observation records but not yet sufficient to establish a single current patient.
_Avoid_: 目前看診病人, 最新病人

**看診中卡片**:
A room-level display of the current HIS observation for the selected clinic session. It is a read-only display projection, not a queue member or one of the official HIS-derived visit states.
_Avoid_: 看診中掛號, 應用程式管理的看診狀態

**候診卡片暫隱**:
A display condition in which a waiting card is temporarily omitted while the same patient's current-patient observation is active. The underlying encounter remains in the waiting sequence and can reappear when the observation ends without completion.
_Avoid_: 從候診隊列移除, 自動完診
