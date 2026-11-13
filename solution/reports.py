"""
Report generation module. Produces all five report files matching output contracts exactly:
1. batch_summary.csv
2. daily_overview.csv
3. lead_report.csv
4. rejections.csv
5. data_quality.json
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from solution.schema import SchemaRegistry, parse_timestamp_ist


@dataclass
class CallInfo:
    call_id: str
    workflow_id: str
    lead_key: tuple[str, str]  # (workflow_id, phone_hash)
    initiated_at_str: str
    initiated_at_dt: datetime
    date_ist: str
    is_connected: bool
    has_telephony_failed: bool
    effective_disposition: str | None
    is_terminal: bool
    is_success: bool


def _build_call_and_lead_aggregates(
    conn: sqlite3.Connection,
    schema_registry: SchemaRegistry,
) -> tuple[
    dict[tuple[str, str], list[CallInfo]],  # lead_key -> list of calls
    dict[tuple[str, str], list[CallInfo]],  # (wf, date_ist) -> list of calls
    int,  # orphan_events count
    int,  # unknown_disposition_events count
]:
    """
    Analyzes the interaction ledger:
    - Resolves call linking to leads.
    - Resolves effective dispositions (overrides > sets).
    - Filters calls that have call.initiated.
    - Counts orphan events and unknown disposition events.
    """
    # 1. Map call_id -> (workflow_id, phone_hash) for calls whose events match a known lead
    cursor = conn.execute(
        """
        SELECT DISTINCT e.call_id, e.workflow_id, e.phone_hash
        FROM events e
        INNER JOIN leads l ON e.workflow_id = l.workflow_id AND e.phone_hash = l.phone_hash
        WHERE e.phone_hash IS NOT NULL AND e.call_id IS NOT NULL AND e.call_id != '';
        """
    )
    call_to_lead: dict[str, tuple[str, str]] = {}
    for row in cursor.fetchall():
        call_to_lead[row["call_id"]] = (row["workflow_id"], row["phone_hash"])

    # 2. Fetch all events and determine linking & unknown dispositions
    all_events = conn.execute(
        """
        SELECT event_id, event_type, workflow_id, call_id, occurred_at_ist,
               date_ist, phone_hash, disposition_code, is_known_disposition
        FROM events;
        """
    ).fetchall()

    orphan_events = 0
    unknown_disposition_events = 0
    events_by_call: dict[str, list[sqlite3.Row]] = defaultdict(list)

    for ev in all_events:
        cid = ev["call_id"]
        wf = ev["workflow_id"]
        ph = ev["phone_hash"]
        lead_key = None

        if ph and (cid in call_to_lead):
            lead_key = call_to_lead[cid]
        elif cid and (cid in call_to_lead):
            # E.g. disposition.override without phone_hash
            lead_key = call_to_lead[cid]

        if not lead_key:
            orphan_events += 1
            continue

        # Linked event -> check unknown disposition
        etype = ev["event_type"]
        if etype.startswith("disposition.") and ev["is_known_disposition"] == 0:
            unknown_disposition_events += 1
            # Unknown disposition events are ignored for effective disposition calculation per spec
            continue

        if cid:
            events_by_call[cid].append(ev)

    # 3. Analyze calls that have call.initiated
    lead_calls: dict[tuple[str, str], list[CallInfo]] = defaultdict(list)
    daily_calls: dict[tuple[str, str], list[CallInfo]] = defaultdict(list)

    for cid, ev_list in events_by_call.items():
        init_ev = next((e for e in ev_list if e["event_type"] == "call.initiated"), None)
        if not init_ev:
            # Per spec: A call is counted only if its call.initiated event is present (and linked)
            continue

        lead_key = call_to_lead[cid]
        wf = lead_key[0]
        init_raw = init_ev["occurred_at_ist"]
        init_dt = parse_timestamp_ist(init_raw) or datetime.min
        date_ist = init_ev["date_ist"] or (init_dt.date().isoformat() if init_dt != datetime.min else "")

        is_connected = any(e["event_type"] == "call.connected" for e in ev_list)
        has_telephony_failed = any(e["event_type"] == "call.telephony_failed" for e in ev_list)

        # Effective disposition:
        # Latest disposition.override by occurred_at; if none, latest disposition.set
        overrides = [
            e for e in ev_list
            if e["event_type"] == "disposition.override" and e["is_known_disposition"] == 1
        ]
        sets = [
            e for e in ev_list
            if e["event_type"] == "disposition.set" and e["is_known_disposition"] == 1
        ]

        eff_disp = None
        if overrides:
            overrides.sort(key=lambda e: parse_timestamp_ist(e["occurred_at_ist"]) or datetime.min)
            eff_disp = overrides[-1]["disposition_code"]
        elif sets:
            sets.sort(key=lambda e: parse_timestamp_ist(e["occurred_at_ist"]) or datetime.min)
            eff_disp = sets[-1]["disposition_code"]

        is_terminal = False
        is_success = False
        if eff_disp:
            disp_info = schema_registry.get_disposition(wf, eff_disp)
            if disp_info:
                is_terminal = disp_info.is_terminal
                is_success = disp_info.is_success

        call_info = CallInfo(
            call_id=cid,
            workflow_id=wf,
            lead_key=lead_key,
            initiated_at_str=init_raw,
            initiated_at_dt=init_dt,
            date_ist=date_ist,
            is_connected=is_connected,
            has_telephony_failed=has_telephony_failed,
            effective_disposition=eff_disp,
            is_terminal=is_terminal,
            is_success=is_success,
        )

        lead_calls[lead_key].append(call_info)
        if date_ist:
            daily_calls[(wf, date_ist)].append(call_info)

    return lead_calls, daily_calls, orphan_events, unknown_disposition_events


def generate_reports(
    conn: sqlite3.Connection,
    output_dir: Path,
    schema_registry: SchemaRegistry,
) -> None:
    """
    Generates all five report files into output_dir.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    lead_calls, daily_calls, orphan_events, unknown_disposition_events = (
        _build_call_and_lead_aggregates(conn, schema_registry)
    )

    # Fetch all leads from database
    leads_cursor = conn.execute(
        """
        SELECT workflow_id, phone_hash, batch_id, external_ref, phone_masked, raw_fields_json
        FROM leads
        ORDER BY workflow_id, external_ref;
        """
    )
    all_leads = leads_cursor.fetchall()

    # Pre-aggregate lead status and bucket
    lead_rows_for_report = []
    batch_buckets = defaultdict(lambda: defaultdict(int))

    for lead in all_leads:
        wf = lead["workflow_id"]
        ph = lead["phone_hash"]
        batch_id = lead["batch_id"]
        ext_ref = lead["external_ref"]
        phone_masked = lead["phone_masked"]

        calls = lead_calls.get((wf, ph), [])
        total_calls = len(calls)
        connected_calls = sum(1 for c in calls if c.is_connected)

        # Bucket evaluation (first match wins per spec)
        # 1. yet_to_reach_out: no calls
        # 2. completed: any call's effective disposition is terminal (is_terminal=true)
        # 3. in_progress_telephony_failed: every call has a call.telephony_failed event
        # 4. in_progress_dialed: everything else
        if total_calls == 0:
            bucket = "yet_to_reach_out"
        elif any(c.is_terminal for c in calls):
            bucket = "completed"
        elif all(c.has_telephony_failed for c in calls):
            bucket = "in_progress_telephony_failed"
        else:
            bucket = "in_progress_dialed"

        batch_buckets[(wf, batch_id)][bucket] += 1
        batch_buckets[(wf, batch_id)]["total"] += 1

        # Most recent call (by initiated time) that has an effective disposition
        calls_with_disp = [c for c in calls if c.effective_disposition is not None]
        if calls_with_disp:
            calls_with_disp.sort(key=lambda c: c.initiated_at_dt, reverse=True)
            current_disposition = calls_with_disp[0].effective_disposition or ""
        else:
            current_disposition = ""

        # Latest call initiated time
        if calls:
            calls_sorted = sorted(calls, key=lambda c: c.initiated_at_dt, reverse=True)
            last_call_at = calls_sorted[0].initiated_at_str
        else:
            last_call_at = ""

        # Ever success: whether any call's effective disposition has is_success=true
        ever_success = "true" if any(c.is_success for c in calls) else "false"

        lead_rows_for_report.append(
            {
                "workflow_id": wf,
                "external_ref": ext_ref,
                "batch_id": batch_id,
                "phone_masked": phone_masked,
                "bucket": bucket,
                "total_calls": total_calls,
                "connected_calls": connected_calls,
                "last_call_at": last_call_at,
                "current_disposition": current_disposition,
                "ever_success": ever_success,
            }
        )

    # 1. Write batch_summary.csv (sorted by workflow_id, batch_id)
    batch_summary_path = output_dir / "batch_summary.csv"
    with open(batch_summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "workflow_id",
                "batch_id",
                "total_leads",
                "yet_to_reach_out",
                "in_progress_telephony_failed",
                "in_progress_dialed",
                "completed",
            ]
        )
        for wf, batch_id in sorted(batch_buckets.keys()):
            bdata = batch_buckets[(wf, batch_id)]
            writer.writerow(
                [
                    wf,
                    batch_id,
                    bdata["total"],
                    bdata["yet_to_reach_out"],
                    bdata["in_progress_telephony_failed"],
                    bdata["in_progress_dialed"],
                    bdata["completed"],
                ]
            )

    # 2. Write daily_overview.csv (sorted by workflow_id, date)
    daily_overview_path = output_dir / "daily_overview.csv"
    with open(daily_overview_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "workflow_id",
                "date",
                "total_calls",
                "connected_calls",
                "connectivity_rate",
                "success_leads",
            ]
        )
        for (wf, dt_str) in sorted(daily_calls.keys()):
            calls_on_date = daily_calls[(wf, dt_str)]
            total_c = len(calls_on_date)
            if total_c == 0:
                continue

            conn_c = sum(1 for c in calls_on_date if c.is_connected)
            conn_rate_str = f"{conn_c / total_c:.4f}"

            # Distinct leads with at least one call on that date where is_success=True
            succ_leads = len(set(c.lead_key for c in calls_on_date if c.is_success))

            writer.writerow([wf, dt_str, total_c, conn_c, conn_rate_str, succ_leads])

    # 3. Write lead_report.csv (sorted by workflow_id, external_ref)
    lead_report_path = output_dir / "lead_report.csv"
    lead_rows_for_report.sort(key=lambda r: (r["workflow_id"], r["external_ref"]))
    with open(lead_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "workflow_id",
                "external_ref",
                "batch_id",
                "phone_masked",
                "bucket",
                "total_calls",
                "connected_calls",
                "last_call_at",
                "current_disposition",
                "ever_success",
            ]
        )
        for r in lead_rows_for_report:
            writer.writerow(
                [
                    r["workflow_id"],
                    r["external_ref"],
                    r["batch_id"],
                    r["phone_masked"],
                    r["bucket"],
                    r["total_calls"],
                    r["connected_calls"],
                    r["last_call_at"],
                    r["current_disposition"],
                    r["ever_success"],
                ]
            )

    # 4. Write rejections.csv (sorted by workflow_id, batch_id, row_number) - No PII
    rejections_path = output_dir / "rejections.csv"
    rej_cursor = conn.execute(
        """
        SELECT workflow_id, batch_id, row_number, reason
        FROM rejections
        ORDER BY workflow_id, batch_id, row_number;
        """
    )
    with open(rejections_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["workflow_id", "batch_id", "row_number", "reason"])
        for row in rej_cursor.fetchall():
            writer.writerow([row["workflow_id"], row["batch_id"], row["row_number"], row["reason"]])

    # 5. Write data_quality.json
    dq_path = output_dir / "data_quality.json"

    # Aggregates from ingested_files
    lead_stats = conn.execute(
        """
        SELECT COALESCE(SUM(non_blank_rows), 0) AS total_rows,
               COALESCE(SUM(rejected_rows), 0) AS rejected_rows,
               COALESCE(SUM(merged_duplicates), 0) AS merged_dups,
               COALESCE(SUM(field_warnings), 0) AS warnings
        FROM ingested_files
        WHERE file_type = 'leads';
        """
    ).fetchone()

    event_stats = conn.execute(
        """
        SELECT COALESCE(SUM(non_blank_rows), 0) AS total_events,
               COALESCE(SUM(malformed_events), 0) AS malformed,
               COALESCE(SUM(duplicate_events), 0) AS duplicate_events
        FROM ingested_files
        WHERE file_type = 'events';
        """
    ).fetchone()

    # Rejections breakdown by reason
    reasons_cursor = conn.execute(
        """
        SELECT reason, COUNT(*) as cnt
        FROM rejections
        GROUP BY reason
        ORDER BY reason;
        """
    )
    rejections_by_reason: dict[str, int] = {}
    for r in reasons_cursor.fetchall():
        rejections_by_reason[r["reason"]] = r["cnt"]

    dq_dict = {
        "lead_rows_total": lead_stats["total_rows"] if lead_stats else 0,
        "lead_rows_rejected": lead_stats["rejected_rows"] if lead_stats else 0,
        "rejections_by_reason": rejections_by_reason,
        "duplicate_lead_rows_merged": lead_stats["merged_dups"] if lead_stats else 0,
        "field_warnings": lead_stats["warnings"] if lead_stats else 0,
        "events_total": event_stats["total_events"] if event_stats else 0,
        "malformed_events": event_stats["malformed"] if event_stats else 0,
        "duplicate_events_dropped": event_stats["duplicate_events"] if event_stats else 0,
        "orphan_events": orphan_events,
        "unknown_disposition_events": unknown_disposition_events,
    }

    with open(dq_path, "w", encoding="utf-8") as f:
        json.dump(dq_dict, f, indent=2)

