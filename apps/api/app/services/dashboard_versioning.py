"""
Dashboard version history: snapshotting, diffing, and restoring.

Split deliberately into pure functions (serialize/diff/next-number — no DB,
fully unit-testable) and one thin DB-touching wrapper (`create_version`),
same "AI decides, code validates" instinct toward keeping the checkable
logic separate from I/O that shows up everywhere else in this codebase.

`create_version` flushes but does not commit — callers (dashboards.py's
route handlers) add it to the same transaction as the mutation it's
snapshotting, same pattern as Phase 12's `log_activity`, so a version row
never exists for an edit that didn't actually get committed.
"""

import uuid

from sqlalchemy.orm import Session

from app.models.dashboard import Dashboard
from app.models.dashboard_version import DashboardVersion
from app.models.dashboard_widget import DashboardWidget


def serialize_widgets_for_snapshot(widgets: list[DashboardWidget]) -> list[dict]:
    """Freezes the current DB widget rows into plain, JSON-safe dicts."""
    return [
        {
            "id": str(w.id),
            "widget_type": w.widget_type.value,
            "title": w.title,
            "dataset_id": str(w.dataset_id) if w.dataset_id else None,
            "config_json": w.config_json,
            "x": w.x,
            "y": w.y,
            "w": w.w,
            "h": w.h,
        }
        for w in widgets
    ]


def next_version_number(existing_numbers: list[int]) -> int:
    return (max(existing_numbers) + 1) if existing_numbers else 1


_DIFF_FIELDS = ("title", "config_json", "x", "y", "w", "h", "dataset_id", "widget_type")


def diff_widget_snapshots(old: list[dict], new: list[dict]) -> dict:
    """
    Compares two widget-snapshot lists (as produced by
    `serialize_widgets_for_snapshot`) keyed on each widget's `id`.

    A widget whose id only exists on one side is "added" or "removed".
    Because a *restore* creates brand-new widget rows with new ids, a
    restore's diff against the version right before it will show as a
    full removed+added set rather than "modified" — that's an accurate
    description of what actually happened at the row level, not a bug;
    the change_summary on a restore's version ("Restored from version N")
    is what makes that readable at a glance instead of just from the diff.
    """
    old_by_id = {w["id"]: w for w in old}
    new_by_id = {w["id"]: w for w in new}

    added = [w for wid, w in new_by_id.items() if wid not in old_by_id]
    removed = [w for wid, w in old_by_id.items() if wid not in new_by_id]

    modified = []
    for wid, new_widget in new_by_id.items():
        old_widget = old_by_id.get(wid)
        if old_widget is None:
            continue
        changed_fields = {
            field: {"before": old_widget.get(field), "after": new_widget.get(field)}
            for field in _DIFF_FIELDS
            if old_widget.get(field) != new_widget.get(field)
        }
        if changed_fields:
            modified.append({"id": wid, "changes": changed_fields})

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged_count": len(new_by_id) - len(modified) - len(added),
    }


def create_version(
    db: Session,
    *,
    dashboard: Dashboard,
    actor_user_id: uuid.UUID | None,
    change_summary: str,
) -> DashboardVersion:
    existing_numbers = [
        n
        for (n,) in db.query(DashboardVersion.version_number)
        .filter(DashboardVersion.dashboard_id == dashboard.id)
        .all()
    ]
    version = DashboardVersion(
        workspace_id=dashboard.workspace_id,
        dashboard_id=dashboard.id,
        version_number=next_version_number(existing_numbers),
        name=dashboard.name,
        widgets_snapshot_json=serialize_widgets_for_snapshot(dashboard.widgets),
        change_summary=change_summary,
        created_by_user_id=actor_user_id,
    )
    db.add(version)
    db.flush()
    return version
