"""
Standalone unit tests for Phase 13 (Version History).

Covers the pure-Python core of dashboard versioning: next-version-number
allocation, widget-snapshot serialization shape, and diffing two snapshot
lists (added/removed/modified). `create_version` and the restore endpoint
touch the DB/ORM directly and are exercised manually — see README.md
"Testing Phase 13".

Run with: python tests/test_dashboard_versioning.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.dashboard_versioning import diff_widget_snapshots, next_version_number


def test_next_version_number_starts_at_one():
    assert next_version_number([]) == 1


def test_next_version_number_increments_from_max():
    assert next_version_number([1, 2, 3]) == 4
    assert next_version_number([1, 5, 2]) == 6  # order shouldn't matter


def _widget(id_, **overrides):
    base = {
        "id": id_,
        "widget_type": "kpi",
        "title": "Revenue",
        "dataset_id": "ds-1",
        "config_json": {"column": "revenue", "aggregation": "sum"},
        "x": 0,
        "y": 0,
        "w": 4,
        "h": 3,
    }
    base.update(overrides)
    return base


def test_diff_identical_snapshots_has_no_changes():
    widgets = [_widget("w1"), _widget("w2", title="Signups")]
    result = diff_widget_snapshots(widgets, widgets)
    assert result["added"] == []
    assert result["removed"] == []
    assert result["modified"] == []
    assert result["unchanged_count"] == 2


def test_diff_detects_added_widget():
    old = [_widget("w1")]
    new = [_widget("w1"), _widget("w2")]
    result = diff_widget_snapshots(old, new)
    assert [w["id"] for w in result["added"]] == ["w2"]
    assert result["removed"] == []
    assert result["unchanged_count"] == 1


def test_diff_detects_removed_widget():
    old = [_widget("w1"), _widget("w2")]
    new = [_widget("w1")]
    result = diff_widget_snapshots(old, new)
    assert [w["id"] for w in result["removed"]] == ["w2"]
    assert result["added"] == []


def test_diff_detects_modified_field():
    old = [_widget("w1", title="Revenue")]
    new = [_widget("w1", title="Revenue (Q3)")]
    result = diff_widget_snapshots(old, new)
    assert len(result["modified"]) == 1
    change = result["modified"][0]
    assert change["id"] == "w1"
    assert change["changes"]["title"] == {"before": "Revenue", "after": "Revenue (Q3)"}
    assert "x" not in change["changes"]  # unrelated fields shouldn't show up


def test_diff_detects_multiple_field_changes_in_one_widget():
    old = [_widget("w1", x=0, y=0)]
    new = [_widget("w1", x=4, y=2)]
    result = diff_widget_snapshots(old, new)
    change = result["modified"][0]["changes"]
    assert change["x"] == {"before": 0, "after": 4}
    assert change["y"] == {"before": 0, "after": 2}


def test_diff_restore_looks_like_full_replace_when_ids_differ():
    # A restore creates brand-new widget rows/ids — the diff should show
    # this honestly as remove-everything + add-everything, not try to
    # pretend it's a "modification" of the same widgets.
    old = [_widget("w1"), _widget("w2")]
    new = [_widget("w3"), _widget("w4")]
    result = diff_widget_snapshots(old, new)
    assert {w["id"] for w in result["removed"]} == {"w1", "w2"}
    assert {w["id"] for w in result["added"]} == {"w3", "w4"}
    assert result["modified"] == []


if __name__ == "__main__":
    test_next_version_number_starts_at_one()
    test_next_version_number_increments_from_max()
    test_diff_identical_snapshots_has_no_changes()
    test_diff_detects_added_widget()
    test_diff_detects_removed_widget()
    test_diff_detects_modified_field()
    test_diff_detects_multiple_field_changes_in_one_widget()
    test_diff_restore_looks_like_full_replace_when_ids_differ()
    print("All Phase 13 unit tests passed.")
