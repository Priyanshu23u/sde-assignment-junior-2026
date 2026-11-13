"""
Unit tests for event processing, append-only ledger, malformed lines, deduplication, and call linking.
"""
from pathlib import Path
import pytest
from solution.database import get_connection, init_db
from solution.ingest_events import ingest_event_file
from solution.ingest_leads import ingest_lead_file
from solution.reports import _build_call_and_lead_aggregates
from solution.schema import SchemaRegistry


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test_events.db"
    init_db(db_file)
    conn = get_connection(db_file)
    yield conn, tmp_path
    conn.close()


def test_event_ingestion_and_deduplication(test_db):
    conn, tmp_path = test_db
    reg = SchemaRegistry()

    jsonl = tmp_path / "events_0001.jsonl"
    lines = [
        # 1. Valid event
        '{"event_id": "evt_001", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "call_001", "occurred_at": "2026-10-01T10:00:00Z", "phone": "+919876543210"}',
        # 2. Duplicate event_id
        '{"event_id": "evt_001", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "call_001", "occurred_at": "2026-10-01T10:00:00Z", "phone": "+919876543210"}',
        # 3. Malformed JSON
        '{"event_id": "evt_002", "broken_json',
        # 4. Missing event_id
        '{"event_type": "call.ended", "workflow_id": "motor_renewal", "call_id": "call_001"}',
        # 5. Valid connected event
        '{"event_id": "evt_003", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "call_001", "occurred_at": "2026-10-01T10:00:10Z", "phone": "+919876543210"}',
    ]
    jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")

    stats = ingest_event_file(conn, jsonl, reg, "drop_1/events/events_0001.jsonl")
    assert stats["non_blank_rows"] == 5
    assert stats["malformed_events"] == 2
    assert stats["duplicate_events"] == 1
    assert stats["valid_events_inserted"] == 2

    # Verify ledger entries
    events = conn.execute("SELECT event_id FROM events ORDER BY event_id;").fetchall()
    assert [e[0] for e in events] == ["evt_001", "evt_003"]


def test_out_of_order_event_lead_linking(test_db):
    """
    Events arrive BEFORE the lead they belong to.
    When lead arrives later, orphan events become linked!
    """
    conn, tmp_path = test_db
    reg = SchemaRegistry()

    # Step 1: Ingest events for phone +919876543210 (lead not yet uploaded)
    jsonl = tmp_path / "events_0001.jsonl"
    jsonl.write_text(
        '{"event_id": "evt_001", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "call_001", "occurred_at": "2026-10-01T10:00:00Z", "phone": "+919876543210"}\n',
        encoding="utf-8",
    )
    ingest_event_file(conn, jsonl, reg, "drop_1/events/events_0001.jsonl")

    # Check aggregate before lead arrives -> should be orphan!
    lead_calls, _, orphans_before, _ = _build_call_and_lead_aggregates(conn, reg)
    assert orphans_before == 1
    assert len(lead_calls) == 0

    # Step 2: Lead arrives in Drop 2
    csv2 = tmp_path / "batch_003.csv"
    csv2.write_text(
        "customer_name,phone,policy_number\n"
        "Aarav Sharma,+919876543210,MOT/2026/100001\n",
        encoding="utf-8",
    )
    ingest_lead_file(conn, csv2, "motor_renewal", reg, "drop_2/leads/motor_renewal/batch_003.csv")

    # Check aggregate after lead arrives -> orphan is resolved and linked!
    lead_calls_after, _, orphans_after, _ = _build_call_and_lead_aggregates(conn, reg)
    assert orphans_after == 0
    assert len(lead_calls_after) == 1
    call = lead_calls_after[("motor_renewal", list(lead_calls_after.keys())[0][1])][0]
    assert call.call_id == "call_001"

