"""
Unit tests for MCP Server productivity tools.
"""

from datetime import datetime, timedelta
import pytest

from mcp_server import (
    calendar_create_event,
    calendar_delete_event,
    calendar_list_events,
    fetch_all_calendar_events,
    gmail_auth_status,
    gmail_send_email,
    load_calendar_events,
    load_simulated_outbox,
    record_outbox_email,
    resolve_date_phrase,
    save_calendar_events,
    search_web,
)


def test_resolve_date_phrase():
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    assert resolve_date_phrase("today") == today_str
    assert resolve_date_phrase("tomorrow") == tomorrow_str
    assert resolve_date_phrase("yesterday") == yesterday_str
    assert resolve_date_phrase("2026-12-25") == "2026-12-25"

    # Test weekday resolution
    friday_str = resolve_date_phrase("friday")
    assert len(friday_str) == 10
    assert friday_str.startswith("202")


def test_calendar_crud_flow():
    test_title = f"Automated Test Meeting {datetime.now().timestamp()}"
    res_create = calendar_create_event(
        title=test_title,
        date="tomorrow",
        start_time="3:00 PM",
        description="Verification test",
        location="Lab Room A",
    )
    assert f"Event '{test_title}' scheduled" in res_create
    assert "ID: evt-" in res_create

    # Extract event ID
    event_id = res_create.split("ID: ")[1].rstrip(").")

    # List events for tomorrow
    res_list = calendar_list_events("tomorrow")
    assert test_title in res_list

    # Delete event
    res_del = calendar_delete_event(event_id)
    assert f"Event '{event_id}' has been cancelled" in res_del

    # List again to ensure deleted
    res_list_after = calendar_list_events("tomorrow")
    assert event_id not in res_list_after


def test_gmail_send_validation():
    # Invalid email
    res1 = gmail_send_email(to_email="invalid_address", subject="Hi", body="Text")
    assert "valid recipient email address is required" in res1

    # Empty subject
    res2 = gmail_send_email(to_email="test@example.com", subject="", body="Text")
    assert "subject is required" in res2

    # Empty body
    res3 = gmail_send_email(to_email="test@example.com", subject="Hi", body="")
    assert "body content is required" in res3


def test_simulated_outbox_recording():
    record = {
        "user_id": "test_user",
        "to": "partner@domain.com",
        "subject": "Greetings",
        "body": "Hello partner",
        "timestamp": datetime.now().isoformat(),
        "status": "simulated_sent",
    }
    record_outbox_email(record)
    outbox = load_simulated_outbox()
    assert len(outbox) > 0
    assert any(o.get("to") == "partner@domain.com" for o in outbox)


def test_search_web_empty_query():
    res = search_web("")
    assert "Please provide a search query" in res
