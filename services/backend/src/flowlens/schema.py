from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flowlens.models import DatasetProfile, SchemaChange, SchemaDiff


def compare_schema(dataset: str, current: DatasetProfile, prior_events: list[dict[str, Any]]) -> SchemaDiff:
    baseline = next((event for event in prior_events if isinstance(event.get("payload"), dict)), None)
    now = datetime.now(timezone.utc).isoformat()
    if baseline is None:
        return SchemaDiff(dataset=dataset, has_baseline=False, changes=[], current_at=now)
    previous = baseline["payload"]
    before = previous.get("profiles", [])
    after = [column.model_dump() for column in current.profiles]
    before_map = {column["name"]: column for column in before}
    after_map = {column["name"]: column for column in after}
    changes: list[SchemaChange] = []
    for name in after_map.keys() - before_map.keys():
        changes.append(SchemaChange(kind="column_added", column=name, after=after_map[name].get("data_type")))
    for name in before_map.keys() - after_map.keys():
        changes.append(SchemaChange(kind="column_removed", column=name, before=before_map[name].get("data_type")))
    for name in before_map.keys() & after_map.keys():
        old, new = before_map[name], after_map[name]
        if old.get("data_type") != new.get("data_type"):
            changes.append(SchemaChange(kind="type_changed", column=name, before=old.get("data_type"), after=new.get("data_type")))
        old_nonnull = old.get("null_count") == 0
        new_nonnull = new.get("null_count") == 0
        if old_nonnull != new_nonnull:
            changes.append(SchemaChange(kind="observed_nullability_changed", column=name, before="no nulls observed" if old_nonnull else "nulls observed", after="no nulls observed" if new_nonnull else "nulls observed"))
    old_order = [column["name"] for column in before]
    new_order = [column["name"] for column in after]
    if old_order != new_order and set(old_order) == set(new_order):
        changes.append(SchemaChange(kind="column_order_changed", column="*", before=", ".join(old_order), after=", ".join(new_order)))
    return SchemaDiff(dataset=dataset, has_baseline=True, changes=changes,
                      previous_at=baseline.get("created_at"), current_at=now)
