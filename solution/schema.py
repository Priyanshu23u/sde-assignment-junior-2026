"""
Schema definitions, header normalisation, field validation, and dispositions catalogue.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from solution.config import DATE_FORMATS, IST, SCHEMAS_DIR


# Header normalisation regex: collapse runs of underscores or whitespace to a single space
HEADER_SPLIT_REGEX = re.compile(r"[_\s]+")

# Decimal parsing regex: optional currency prefix, comma-separated digits, optional decimal part
DECIMAL_REGEX = re.compile(
    r"^(?:(?:₹|rs\.?|inr)\s*)?([0-9,]+(?:\.[0-9]+)?)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DispositionInfo:
    workflow_id: str
    code: str
    label: str
    is_terminal: bool
    is_success: bool


@dataclass(frozen=True)
class SchemaField:
    name: str
    type: str
    required: bool
    pii: bool = False
    reference: bool = False
    aliases: tuple[str, ...] = ()
    values: tuple[str, ...] = ()  # for enum
    default: str | None = None


@dataclass(frozen=True)
class WorkflowSchema:
    workflow_id: str
    description: str
    fields: tuple[SchemaField, ...]
    field_by_name: dict[str, SchemaField]
    reference_field: str

    def get_reference_field_name(self) -> str:
        return self.reference_field


def normalize_header(header: str) -> str:
    """
    Normalises a header: lower-case it, trim it, and replace every run of '_' or
    whitespace with a single space.
    """
    if not header:
        return ""
    cleaned = header.strip().lower()
    return HEADER_SPLIT_REGEX.sub(" ", cleaned)


def parse_phone(raw: Any) -> str | None:
    """
    Validates and normalises an Indian mobile number:
    1. Strip every non-digit.
    2. If 12 digits starting with '91', drop the '91'.
    3. If 11 digits starting with '0', drop the '0'.
    4. Valid ONLY if exactly 10 digits and starts with 6, 7, 8, or 9.
    5. Returns canonically as '+91XXXXXXXXXX'.
    Returns None if invalid.
    """
    if raw is None:
        return None
    raw_str = str(raw).strip()
    if not raw_str:
        return None

    digits = re.sub(r"\D", "", raw_str)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    return None


def parse_date(raw: Any) -> str | None:
    """
    Validates and normalises dates:
    Accepts: 'YYYY-MM-DD', 'DD/MM/YYYY', 'DD-MM-YYYY', 'DD-Mon-YYYY' (e.g. 01-Dec-2026).
    Dates are day-first. Month name is case-insensitive.
    Impossible dates (like 31/02) are invalid.
    Returns canonical 'YYYY-MM-DD'.
    Returns None if invalid.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_decimal(raw: Any) -> float | None:
    """
    Validates and normalises decimal amounts:
    Optionally prefixed by '₹', 'Rs', 'Rs.', or 'INR' (case-insensitive, optional space).
    Commas are thousands separators. Must be >= 0. Round to 2 decimal places.
    Returns float rounded to 2 places, or None if invalid.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    m = DECIMAL_REGEX.match(s)
    if not m:
        return None

    num_str = m.group(1).replace(",", "")
    try:
        val = float(num_str)
        if val < 0:
            return None
        return round(val, 2)
    except ValueError:
        return None


def parse_enum(raw: Any, allowed_values: tuple[str, ...]) -> str | None:
    """
    Compares case-insensitively against allowed values and stores lower-case.
    Returns lower-case string if matched, else None.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    val = s.lower()
    if val in allowed_values:
        return val
    return None


def parse_timestamp_ist(raw: str) -> datetime | None:
    """
    Parses event timestamps which come in three forms:
    1. UTC ('...Z')
    2. Offset ('...+05:30')
    3. Naive ('2026-10-03 10:01:00') -> Naive means IST per spec.
    Returns timezone-aware datetime in IST.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None

    try:
        if s.endswith("Z"):
            dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return dt.astimezone(IST)
        elif "T" in s:
            dt = datetime.fromisoformat(s)
            return dt.astimezone(IST)
        else:
            # Naive means IST
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
            return dt
    except Exception:
        return None


class SchemaRegistry:
    """Registry holding workflow schemas and disposition catalogue."""

    def __init__(self, schemas_dir: Path | None = None):
        self.dir = schemas_dir or SCHEMAS_DIR
        self._schemas: dict[str, WorkflowSchema] = {}
        self._dispositions: dict[tuple[str, str], DispositionInfo] = {}
        self._load_all()

    def _load_all(self) -> None:
        # Load workflow JSON schemas
        for p in self.dir.glob("*.json"):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            wf_id = data["workflow_id"]
            fields = []
            ref_field = ""
            for fd in data["fields"]:
                field = SchemaField(
                    name=fd["name"],
                    type=fd["type"],
                    required=fd.get("required", False),
                    pii=fd.get("pii", False),
                    reference=fd.get("reference", False),
                    aliases=tuple(fd.get("aliases", [])),
                    values=tuple(fd.get("values", [])),
                    default=fd.get("default"),
                )
                fields.append(field)
                if field.reference:
                    ref_field = field.name

            wf_schema = WorkflowSchema(
                workflow_id=wf_id,
                description=data.get("description", ""),
                fields=tuple(fields),
                field_by_name={f.name: f for f in fields},
                reference_field=ref_field,
            )
            self._schemas[wf_id] = wf_schema

        # Load dispositions CSV
        disp_file = self.dir / "dispositions.csv"
        if disp_file.exists():
            with open(disp_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    wf = row["workflow_id"].strip()
                    code = row["code"].strip()
                    label = row["label"].strip()
                    is_term = row["is_terminal"].strip().lower() == "true"
                    is_succ = row["is_success"].strip().lower() == "true"
                    d_info = DispositionInfo(
                        workflow_id=wf,
                        code=code,
                        label=label,
                        is_terminal=is_term,
                        is_success=is_succ,
                    )
                    self._dispositions[(wf, code)] = d_info

    def get_workflow_schema(self, workflow_id: str) -> WorkflowSchema:
        if workflow_id not in self._schemas:
            raise KeyError(f"Workflow '{workflow_id}' not found in schemas")
        return self._schemas[workflow_id]

    def get_disposition(self, workflow_id: str, code: str) -> DispositionInfo | None:
        return self._dispositions.get((workflow_id, code))

    def is_known_disposition(self, workflow_id: str, code: str) -> bool:
        return (workflow_id, code) in self._dispositions

