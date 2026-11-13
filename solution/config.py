"""
Configuration and constants for the reporting pipeline.
"""
from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

# Base Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = PROJECT_ROOT / "assignment-a-data" / "schemas"
DEFAULT_DB_PATH = PROJECT_ROOT / "ledger.db"

# Database Configuration
DATABASE_PATH = Path(os.environ.get("AU_DATABASE_PATH", str(DEFAULT_DB_PATH)))

# Timezone Configuration (Naive means IST per spec)
IST = timezone(timedelta(hours=5, minutes=30))

# PII Protection Secrets
# A master secret used for HMAC-SHA256 blind indexing and authenticated encryption.
# In production, this is supplied via a secure vault or environment variable.
PII_SECRET_KEY = os.environ.get("AU_PII_SECRET", "agentic-universe-takehome-2026-pii-secret-key-42").encode("utf-8")

# Date and Numeric Formats
DATE_FORMATS = [
    "%Y-%m-%d",   # 2026-10-01
    "%d/%m/%Y",   # 01/10/2026 (day-first)
    "%d-%m-%Y",   # 01-10-2026 (day-first)
    "%d-%b-%Y",   # 01-Dec-2026 (month name case-insensitive)
]

# Workflows Supported
SUPPORTED_WORKFLOWS = ["motor_renewal", "policy_feedback"]

