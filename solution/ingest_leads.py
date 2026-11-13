"""
Lead ingestion module: header matching, rejection in schema order,
field warning tracking, PII protection, duplicate merging, and idempotency.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from solution.crypto import blind_index_phone, encrypt_pii, mask_phone
from solution.database import transaction
from solution.schema import (
    SchemaRegistry,
    WorkflowSchema,
    normalize_header,
    parse_date,
    parse_decimal,
    parse_enum,
    parse_phone,
)


def _compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _is_blank_row(row: list[str]) -> bool:
    """A blank row is one where every cell is empty or whitespace."""
    return all(not cell or not cell.strip() for cell in row)


def _build_header_map(raw_headers: list[str], schema: WorkflowSchema) -> dict[int, str]:
    """
    Matches raw CSV headers to schema fields:
    To normalise a header, lower-case it, trim it, and replace every run of '_' or
    whitespace with a single space. Match against field name or aliases (normalised the same way).
    Columns that match nothing are ignored.
    """
    header_map: dict[int, str] = {}
    for idx, raw_h in enumerate(raw_headers):
        norm_h = normalize_header(raw_h)
        for field in schema.fields:
            candidates = [normalize_header(field.name)] + [
                normalize_header(alias) for alias in field.aliases
            ]
            if norm_h in candidates:
                header_map[idx] = field.name
                break
    return header_map


def ingest_lead_file(
    conn: sqlite3.Connection,
    file_path: Path,
    workflow_id: str,
    schema_registry: SchemaRegistry,
    drop_relative_path: str | None = None,
) -> dict[str, int]:
    """
    Ingests a single lead CSV file into the database.
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
        # Already ingested; no-op
        return {
            "skipped": 1,
            "non_blank_rows": 0,
            "rejected_rows": 0,
            "merged_duplicates": 0,
            "field_warnings": 0,
        }

    schema = schema_registry.get_workflow_schema(workflow_id)
    batch_id = file_path.stem

    non_blank_rows = 0
    rejected_rows = 0
    merged_duplicates = 0
    field_warnings = 0

    rejections_to_insert: list[tuple[str, str, int, str, str]] = []

    with open(file_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            raw_headers = next(reader)
        except StopIteration:
            # Empty file
            raw_headers = []

        header_map = _build_header_map(raw_headers, schema)

        with transaction(conn):
            for row in reader:
                if _is_blank_row(row):
                    continue

                non_blank_rows += 1
                row_dict: dict[str, str] = {}
                for idx, cell in enumerate(row):
                    if idx in header_map:
                        fname = header_map[idx]
                        trimmed = cell.strip()
                        if trimmed:
                            row_dict[fname] = trimmed

                # 1. Walk fields in schema order to check for rejection
                rejected = False
                for field in schema.fields:
                    fname = field.name
                    raw_val = row_dict.get(fname)

                    if field.required:
                        if not raw_val:
                            reason = f"missing_required:{fname}"
                            rejections_to_insert.append(
                                (workflow_id, batch_id, non_blank_rows, reason, rel_path)
                            )
                            rejected = True
                            rejected_rows += 1
                            break

                        if field.type == "phone":
                            p = parse_phone(raw_val)
                            if not p:
                                reason = "invalid_phone"
                                rejections_to_insert.append(
                                    (workflow_id, batch_id, non_blank_rows, reason, rel_path)
                                )
                                rejected = True
                                rejected_rows += 1
                                break

                if rejected:
                    continue

                # 2. Row is valid -> Parse and normalise all fields
                parsed_fields: dict[str, Any] = {}
                for field in schema.fields:
                    fname = field.name
                    raw_val = row_dict.get(fname)
                    ftype = field.type

                    if raw_val is None:
                        parsed_fields[fname] = None
                        continue

                    if ftype == "string":
                        parsed_fields[fname] = raw_val
                    elif ftype == "phone":
                        parsed_fields[fname] = parse_phone(raw_val)
                    elif ftype == "date":
                        dt_val = parse_date(raw_val)
                        if dt_val is not None:
                            parsed_fields[fname] = dt_val
                        else:
                            parsed_fields[fname] = None
                            if not field.required:
                                field_warnings += 1
                    elif ftype == "decimal":
                        dec_val = parse_decimal(raw_val)
                        if dec_val is not None:
                            parsed_fields[fname] = dec_val
                        else:
                            parsed_fields[fname] = None
                            if not field.required:
                                field_warnings += 1
                    elif ftype == "enum":
                        enum_val = parse_enum(raw_val, field.values)
                        if enum_val is not None:
                            parsed_fields[fname] = enum_val
                        else:
                            parsed_fields[fname] = None
                            if not field.required:
                                field_warnings += 1

                canonical_phone = parsed_fields["phone"]
                phone_hash = blind_index_phone(canonical_phone)
                now_iso = datetime.now(timezone.utc).isoformat()

                # 3. Check for existing lead (duplicate handling)
                existing = conn.execute(
                    "SELECT lead_id, raw_fields_json, batch_id, name_encrypted, external_ref FROM leads WHERE workflow_id = ? AND phone_hash = ?;",
                    (workflow_id, phone_hash),
                ).fetchone()

                if existing:
                    merged_duplicates += 1
                    lead_id = existing["lead_id"]
                    existing_fields = json.loads(existing["raw_fields_json"])

                    # Overwrite only non-null fields (last write wins) for non-PII attributes
                    for k, v in parsed_fields.items():
                        if not schema.field_by_name[k].pii and v is not None:
                            existing_fields[k] = v

                    # Apply schema defaults after merging to fields that are still null
                    for field in schema.fields:
                        if not field.pii and field.default is not None and existing_fields.get(field.name) is None:
                            existing_fields[field.name] = field.default

                    ref_field = schema.get_reference_field_name()
                    ext_ref = existing_fields.get(ref_field, existing["external_ref"])
                    
                    # Update encrypted customer name if a new non-null value is provided
                    new_name = parsed_fields.get("customer_name")
                    name_encrypted = encrypt_pii(new_name) if new_name else existing["name_encrypted"]

                    conn.execute(
                        """
                        UPDATE leads
                        SET external_ref = ?,
                            name_encrypted = ?,
                            raw_fields_json = ?,
                            updated_at = ?
                        WHERE lead_id = ?;
                        """,
                        (ext_ref, name_encrypted, json.dumps(existing_fields), now_iso, lead_id),
                    )
                else:
                    # Apply schema defaults to fields that are null for new lead
                    for field in schema.fields:
                        if field.default is not None and parsed_fields.get(field.name) is None:
                            parsed_fields[field.name] = field.default

                    # Retain only non-PII attributes in raw_fields_json to guarantee zero plaintext PII at rest
                    non_pii_fields = {
                        k: v for k, v in parsed_fields.items() if not schema.field_by_name[k].pii
                    }

                    # First valid row creates the lead permanently with this batch_id
                    ref_field = schema.get_reference_field_name()
                    ext_ref = non_pii_fields.get(ref_field, "")
                    phone_masked = mask_phone(canonical_phone)
                    phone_encrypted = encrypt_pii(canonical_phone)
                    name_val = parsed_fields.get("customer_name", "")
                    name_encrypted = encrypt_pii(name_val) if name_val else ""

                    conn.execute(
                        """
                        INSERT INTO leads (
                            workflow_id, phone_hash, batch_id, external_ref,
                            phone_masked, phone_encrypted, name_encrypted,
                            raw_fields_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            workflow_id,
                            phone_hash,
                            batch_id,
                            ext_ref,
                            phone_masked,
                            phone_encrypted,
                            name_encrypted,
                            json.dumps(non_pii_fields),
                            now_iso,
                            now_iso,
                        ),
                    )

            # Insert batch rejections
            if rejections_to_insert:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO rejections (workflow_id, batch_id, row_number, reason, file_path)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    rejections_to_insert,
                )

            # Record file ingestion
            conn.execute(
                """
                INSERT OR REPLACE INTO ingested_files (
                    file_path, file_hash, file_type, workflow_id, batch_id,
                    non_blank_rows, rejected_rows, merged_duplicates,
                    field_warnings, malformed_events, duplicate_events, ingested_at
                ) VALUES (?, ?, 'leads', ?, ?, ?, ?, ?, ?, 0, 0, ?);
                """,
                (
                    rel_path,
                    file_hash,
                    workflow_id,
                    batch_id,
                    non_blank_rows,
                    rejected_rows,
                    merged_duplicates,
                    field_warnings,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    return {
        "skipped": 0,
        "non_blank_rows": non_blank_rows,
        "rejected_rows": rejected_rows,
        "merged_duplicates": merged_duplicates,
        "field_warnings": field_warnings,
    }

