"""
Run with: python tests/test_dashboard_generator.py

Covers everything except the actual Claude call in ai_select_widgets
(needs a live API key — see README's "Testing Phase 9" section). The
validation logic that call feeds into (_parse_and_validate) IS covered
here since it's pure parsing/validation with no network dependency.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.dashboard_generator import (
    DashboardGeneratorError,
    GRID_COLS,
    _parse_and_validate,
    build_candidates,
    pack_layout,
    select_fallback,
)

_COLUMN_SUMMARIES = [
    {"column": "revenue", "type": "numeric", "stats": {"mean": 1000}},
    {"column": "cost", "type": "numeric", "stats": {"mean": 500}},
    {"column": "department", "type": "categorical", "stats": {}},
]
_CHART_SUGGESTIONS = [
    {"chart_type": "histogram", "title": "Distribution of revenue", "x": "revenue", "y": None, "reason": "numeric"},
    {"chart_type": "bar", "title": "Count by department", "x": "department", "y": None, "reason": "categorical"},
]


def test_build_candidates_includes_kpi_per_numeric_column():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    kpis = [c for c in candidates if c["widget_type"] == "kpi"]
    assert len(kpis) == 2  # revenue, cost


def test_build_candidates_includes_charts():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    charts = [c for c in candidates if c["widget_type"] == "chart"]
    assert len(charts) == 2


def test_build_candidates_always_includes_one_table():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    tables = [c for c in candidates if c["widget_type"] == "table"]
    assert len(tables) == 1


def test_build_candidates_kpi_config_references_real_column():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    kpi = next(c for c in candidates if c["widget_type"] == "kpi")
    assert kpi["config_json"]["column"] in ("revenue", "cost")


def test_select_fallback_respects_max_widgets():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    result = select_fallback(candidates, max_widgets=3)
    assert len(result["widgets"]) <= 3


def test_select_fallback_returns_a_dashboard_name():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    result = select_fallback(candidates)
    assert isinstance(result["dashboard_name"], str) and result["dashboard_name"]


def test_pack_layout_never_exceeds_grid_width():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    fallback = select_fallback(candidates)
    positioned = pack_layout(fallback["widgets"])
    for p in positioned:
        assert p["x"] + p["w"] <= GRID_COLS


def test_pack_layout_assigns_every_widget_a_position():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    fallback = select_fallback(candidates)
    positioned = pack_layout(fallback["widgets"])
    assert len(positioned) == len(fallback["widgets"])
    for p in positioned:
        assert all(k in p for k in ("x", "y", "w", "h"))


def test_parse_and_validate_accepts_valid_response():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    response = '{"dashboard_name": "Sales Overview", "widgets": [{"candidate_index": 0, "title": "Revenue KPI"}]}'
    result = _parse_and_validate(response, candidates)
    assert result["dashboard_name"] == "Sales Overview"
    assert result["widgets"][0]["title"] == "Revenue KPI"


def test_parse_and_validate_skips_out_of_range_index():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    response = (
        '{"dashboard_name": "Test", "widgets": '
        '[{"candidate_index": 0, "title": "OK"}, {"candidate_index": 999, "title": "Bad"}]}'
    )
    result = _parse_and_validate(response, candidates)
    assert len(result["widgets"]) == 1
    assert result["widgets"][0]["title"] == "OK"


def test_parse_and_validate_raises_when_all_indices_invalid():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    response = '{"dashboard_name": "Test", "widgets": [{"candidate_index": 999}]}'
    try:
        _parse_and_validate(response, candidates)
        raise AssertionError("Expected DashboardGeneratorError")
    except DashboardGeneratorError:
        pass


def test_parse_and_validate_raises_on_malformed_json():
    candidates = build_candidates(_COLUMN_SUMMARIES, _CHART_SUGGESTIONS)
    try:
        _parse_and_validate("not json", candidates)
        raise AssertionError("Expected DashboardGeneratorError")
    except DashboardGeneratorError:
        pass


def test_parse_and_validate_caps_at_six_widgets():
    candidates = build_candidates(
        [{"column": f"col_{i}", "type": "numeric", "stats": {"mean": 1}} for i in range(10)],
        [],
    )
    widgets = [{"candidate_index": i, "title": f"KPI {i}"} for i in range(10)]
    import json

    response = json.dumps({"dashboard_name": "Big", "widgets": widgets})
    result = _parse_and_validate(response, candidates)
    assert len(result["widgets"]) <= 6


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\nAll {len(tests)} Phase 9 unit tests passed.")
