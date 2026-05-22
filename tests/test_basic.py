"""Tests for aw-import-timely."""

from aw_import_timely import build_events, _format_duration


def test_build_events_typical():
    """Test converting typical Timely hour entries to AW events."""
    entries = [
        {
            "id": 1,
            "project": 100,
            "user": 200,
            "hours": "2025-03-15",
            "minutes": 480,
            "note": "Worked on feature X",
            "billable": True,
        },
        {
            "id": 2,
            "project": 101,
            "user": 200,
            "hours": "2025-03-16",
            "minutes": 60,
            "note": "Meeting",
            "billable": False,
        },
    ]
    projects = {100: "Project Alpha", 101: "Project Beta"}
    clients = {50: "Client Corp"}

    events = build_events(entries, projects, clients)

    assert len(events) == 2

    # First event
    assert events[0]["timestamp"] == "2025-03-15T00:00:00+00:00"
    assert events[0]["duration"] == 28800.0  # 480 min * 60
    assert events[0]["data"]["title"] == "Worked on feature X"
    assert events[0]["data"]["project"] == "Project Alpha"
    assert events[0]["data"]["client"] == ""
    assert events[0]["data"]["billable"] is True

    # Second event
    assert events[1]["timestamp"] == "2025-03-16T00:00:00+00:00"
    assert events[1]["duration"] == 3600.0  # 60 min * 60
    assert events[1]["data"]["project"] == "Project Beta"
    assert events[1]["data"]["billable"] is False


def test_build_events_skips_empty():
    """Test that zero-minute entries are skipped."""
    entries = [
        {
            "id": 1,
            "project": 100,
            "hours": "2025-03-15",
            "minutes": 0,
            "note": "Zero minutes",
            "billable": False,
        },
        {
            "id": 2,
            "project": 100,
            "hours": "2025-03-16",
            "minutes": None,
            "note": "No minutes",
            "billable": False,
        },
    ]
    events = build_events(entries, {100: "P"}, {})
    assert len(events) == 0


def test_build_events_no_date():
    """Test that entries without a date are skipped."""
    entries = [
        {
            "id": 1,
            "project": 100,
            "minutes": 60,
            "note": "No date",
            "billable": False,
        },
    ]
    events = build_events(entries, {100: "P"}, {})
    assert len(events) == 0


def test_build_events_no_project():
    """Test entries without a project use fallback."""
    entries = [
        {
            "id": 1,
            "hours": "2025-03-15",
            "minutes": 60,
            "note": "No project",
            "billable": False,
        },
    ]
    events = build_events(entries, {}, {})
    assert len(events) == 1
    assert events[0]["data"]["project"] == "No project"


def test_format_duration():
    """Test duration formatting."""
    assert _format_duration(3600) == "1h 0m"
    assert _format_duration(3660) == "1h 1m"
    assert _format_duration(60) == "1m 0s"
    assert _format_duration(0) == "0m 0s"
