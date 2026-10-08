# Separate preregistration from temporary absence

Status: accepted

Treat `TREAT=C` as the HIS-derived 已約未到 state. Only HIS synchronization may move that encounter into 候診中; staff cannot manually mark it present. Treat 已掛暫離 as a local staff decision applied to a waiting encounter because HIS cannot represent temporary absence. When the front desk returns an encounter to 候診中, append it to the tail of the same room-and-session queue. Display the lists in the order 候診中, 已掛暫離, 已約未到. This keeps HIS registration state separate from staff-observed presence.
