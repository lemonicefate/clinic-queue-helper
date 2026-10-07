"""Replaceable encounter identity and queue-number resolvers."""

from __future__ import annotations

from typing import Any, Mapping


def _value(fields: Mapping[str, Any], name: str) -> str:
    for key, value in fields.items():
        if key.upper() == name.upper():
            return "" if value is None else str(value).strip()
    return ""


def resolve_encounter_key(fields: Mapping[str, Any], recno: int | str | None = None) -> str:
    """Resolve a business key without treating a DBF record number as identity."""
    relkey = _value(fields, "RELKEY")
    if relkey:
        return f"relkey:{relkey}"

    sys_2015 = _value(fields, "SYS_2015")
    if sys_2015:
        return (
            f"sys2015:{sys_2015}"
            f"|tetday:{_value(fields, 'TETDAY')}"
            f"|num:{_value(fields, 'NUM')}"
            f"|gino1:{_value(fields, 'GINO1')}"
        )

    locator = recno if recno is not None else _value(fields, "_RECNO")
    return (
        f"fallback:recno:{locator}"
        f"|tetday:{_value(fields, 'TETDAY')}"
        f"|num:{_value(fields, 'NUM')}"
        f"|ccdate:{_value(fields, 'CCDATE')}"
        f"|cctime:{_value(fields, 'CCTIME')}"
    )


def parse_queue_number(raw_gino1: Any, chart_number: Any = "") -> str:
    """Display the last four GINO1 digits, falling back to the chart number."""
    raw = "" if raw_gino1 is None else str(raw_gino1).strip()
    suffix = raw[-4:]
    if len(suffix) == 4 and suffix.isdigit():
        return str(int(suffix))
    return "" if chart_number is None else str(chart_number).strip()
