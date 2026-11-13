"""
Event ingestion module: JSON parsing, event deduplication, append-only ledger persistence,
and disposition catalogue classification.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from solution.crypto import blind_index_phone, encrypt_pii
from solution.database import transaction
from solution.schema import SchemaRegistry, parse_phone, parse_timestamp_ist


def _compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def ingest_event_file(
    conn: sqlite3.Connection,
    file_path: Path,
    schema_registry: SchemaRegistry,
    drop_relative_path: str | None = None,
) -> dict[str, int]:
    """
    Ingests a single events JSON Lines file into the append-only ledger.
    Guarantees idempotency via file checksum and relative path tracking.
    """
    rel_path = drop_relative_path or str(file_path)
    file_hash = _compute_file_sha256(file_path)

    # Check idempotency: has this exact file been ingested?
    cursor = conn.execute(
        "SELECT file_path FROM ingested_files WHERE file_path = ? AND file_hash = ?;",
        (rel_path, file_hash),
    )
    if cursor.fetchone():
        return {
            "skipped": 1,
            "non_blank_rows": 0,
            "malformed_events": 0,
            "duplicate_events": 0,
            "valid_events_inserted": 0,
        }

    non_blank_lines = 0
    malformed_events = 0
    duplicate_events = 0
    valid_events_inserted = 0

    # In-memory set for deduplication against already committed events in DB + current batch
    seen_in_db = set(row[0] for row in conn.execute("SELECT event_id FROM events;").fetchall())

    records_to_insert: list[tuple[Any, ...]] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue

            non_blank_lines += 1

            # 1. Malformed check
            try:
                ev = json.loads(line_str)
            except Exception:
                malformed_events += 1
                continue

            if not isinstance(ev, dict):
                malformed_events += 1
                continue

            event_id = ev.get("event_id")
            if not event_id or not isinstance(event_id, str):
                malformed_events += 1
                continue

            # 2. Duplicate check
            if event_id in seen_in_db:
                duplicate_events += 1
                continue
            seen_in_db.add(event_id)

            # Valid distinct event -> normalise and prepare for ledger
            event_type = str(ev.get("event_type", ""))
            workflow_id = str(ev.get("workflow_id", ""))
            call_id = str(ev.get("call_id", ""))
            raw_occurred = str(ev.get("occurred_at", ""))

            ts_ist = parse_timestamp_ist(raw_occurred)
            if ts_ist is not None:
                occurred_at_ist = ts_ist.strftime("%Y-%m-%dT%H:%M:%S+05:30")
                date_ist = ts_ist.date().isoformat()
            else:
                occurred_at_ist = raw_occurred
                date_ist = ""

            # Phone processing & PII protection
            raw_phone = ev.get("phone")
            canonical_phone = parse_phone(raw_phone) if raw_phone else None
            phone_hash = blind_index_phone(canonical_phone) if canonical_phone else None
            phone_encrypted = encrypt_pii(canonical_phone) if canonical_phone else None

            payload = ev.get("payload", {})
            payload_json = json.dumps(payload)

            # Disposition catalogue lookup
            disposition_code = None
            is_known = 1
            if event_type.startswith("disposition."):
                disposition_code = payload.get("disposition_code") if isinstance(payload, dict) else None
                if disposition_code:
                    if not schema_registry.is_known_disposition(workflow_id, str(disposition_code)):
                        is_known = 0
                else:
                    is_known = 0

            records_to_insert.append(
                (
                    event_id,
                    event_type,
                    workflow_id,
                    call_id,
                    raw_occurred,
                    occurred_at_ist,
                    date_ist,
                    phone_hash,
                    phone_encrypted,
                    payload_json,
                    disposition_code,
                    is_known,
                    rel_path,
                    now_iso,
                )
            )

    # Insert events and record file completion atomically
    with transaction(conn):
        if records_to_insert:
            conn.executemany(
                """
                INSERT OR IGNORE INTO events (
                    event_id, event_type, workflow_id, call_id, occurred_at,
                    occurred_at_ist, date_ist, phone_hash, phone_encrypted,
                    payload_json, disposition_code, is_known_disposition,
                    file_path, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                records_to_insert,
            )
            valid_events_inserted = len(records_to_insert)

        conn.execute(
            """
            INSERT OR REPLACE INTO ingested_files (
                file_path, file_hash, file_type, workflow_id, batch_id,
                non_blank_rows, rejected_rows, merged_duplicates,
                field_warnings, malformed_events, duplicate_events, ingested_at
            ) VALUES (?, ?, 'events', NULL, NULL, ?, 0, 0, 0, ?, ?, ?);
            """,
            (
                rel_path,
                file_hash,
                non_blank_lines,
                malformed_events,
                duplicate_events,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    return {
        "skipped": 0,
        "non_blank_rows": non_blank_lines,
        "malformed_events": malformed_events,
        "duplicate_events": duplicate_events,
        "valid_events_inserted": valid_events_inserted,
    }

