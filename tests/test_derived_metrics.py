"""
Unit tests for derived facts: effective dispositions, call counting, bucket rules, and metrics.
"""
from pathlib import Path
import pytest
from solution.database import get_connection, init_db
from solution.ingest_events import ingest_event_file
from solution.ingest_leads import ingest_lead_file
from solution.reports import _build_call_and_lead_aggregates
from solution.schema import SchemaRegistry


@pytest.fixture
def populated_env(tmp_path: Path):
    db_file = tmp_path / "test_metrics.db"
    init_db(db_file)
    conn = get_connection(db_file)
    reg = SchemaRegistry()

    # Create 4 leads for bucket testing
    leads_csv = tmp_path / "batch_001.csv"
    leads_csv.write_text(
        "customer_name,phone,policy_number\n"
        "Lead Zero,+919876543210,MOT/001\n"
        "Lead Term,+919876543211,MOT/002\n"
        "Lead Fail,+919876543212,MOT/003\n"
        "Lead Dial,+919876543213,MOT/004\n",
        encoding="utf-8",
    )
    ingest_lead_file(conn, leads_csv, "motor_renewal", reg, "drop_1/leads/motor_renewal/batch_001.csv")
    yield conn, tmp_path, reg
    conn.close()


def test_effective_disposition_override_and_unknown_dispositions(populated_env):
    conn, tmp_path, reg = populated_env

    events_file = tmp_path / "events.jsonl"
    lines = [
        # Call 1 on Lead Term:
        # Initiated, connected, disposition.set = 'callback_requested' (non-terminal)
        # disposition.override = 'renewed' (terminal, success)
        # disposition.set = 'unknown_code' (should be ignored!)
        '{"event_id": "e1", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:00:00Z", "phone": "+919876543211"}',
        '{"event_id": "e2", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:00:05Z", "phone": "+919876543211"}',
        '{"event_id": "e3", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:01:00Z", "phone": "+919876543211", "payload": {"disposition_code": "callback_requested"}}',
        '{"event_id": "e4", "event_type": "disposition.override", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T11:00:00Z", "payload": {"disposition_code": "renewed"}}',
        '{"event_id": "e5", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T12:00:00Z", "phone": "+919876543211", "payload": {"disposition_code": "INVALID_UNKNOWN"}}',

        # Call 2 on Lead Fail:
        # 2 calls, both have telephony_failed
        '{"event_id": "e6", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c2", "occurred_at": "2026-10-02T10:00:00Z", "phone": "+919876543212"}',
        '{"event_id": "e7", "event_type": "call.telephony_failed", "workflow_id": "motor_renewal", "call_id": "c2", "occurred_at": "2026-10-02T10:00:02Z", "phone": "+919876543212"}',
        '{"event_id": "e8", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c3", "occurred_at": "2026-10-02T14:00:00Z", "phone": "+919876543212"}',
        '{"event_id": "e9", "event_type": "call.telephony_failed", "workflow_id": "motor_renewal", "call_id": "c3", "occurred_at": "2026-10-02T14:00:02Z", "phone": "+919876543212"}',

        # Call 3 on Lead Dial:
        # Connected, non-terminal disposition
        '{"event_id": "e10", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c4", "occurred_at": "2026-10-03T09:00:00Z", "phone": "+919876543213"}',
        '{"event_id": "e11", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c4", "occurred_at": "2026-10-03T09:00:05Z", "phone": "+919876543213"}',
        '{"event_id": "e12", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c4", "occurred_at": "2026-10-03T09:01:00Z", "phone": "+919876543213", "payload": {"disposition_code": "payment_link_sent"}}',
    ]
    events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ingest_event_file(conn, events_file, reg, "drop_1/events/events.jsonl")

    lead_calls, daily_calls, orphans, unknown_disps = _build_call_and_lead_aggregates(conn, reg)
    assert unknown_disps == 1  # INVALID_UNKNOWN counted
    assert orphans == 0

    # Call 1 effective disposition must be 'renewed' because override beats set and invalid is ignored
    c1_calls = [c for c in lead_calls.values() for c in c if c.call_id == "c1"]
    assert len(c1_calls) == 1
    assert c1_calls[0].effective_disposition == "renewed"
    assert c1_calls[0].is_terminal is True
    assert c1_calls[0].is_success is True


def test_call_without_initiated_is_not_counted(populated_env):
    """A call with call.connected but NO call.initiated must not be counted."""
    conn, tmp_path, reg = populated_env

    events_file = tmp_path / "events_no_init.jsonl"
    events_file.write_text(
        '{"event_id": "e_conn", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c_orphan_init", "occurred_at": "2026-10-01T10:00:00Z", "phone": "+919876543210"}\n',
        encoding="utf-8",
    )
    ingest_event_file(conn, events_file, reg, "drop_1/events/events_no_init.jsonl")

    lead_calls, _, _, _ = _build_call_and_lead_aggregates(conn, reg)
    # Total calls for this lead should be 0 because call.initiated was missing!
    assert len(lead_calls) == 0
