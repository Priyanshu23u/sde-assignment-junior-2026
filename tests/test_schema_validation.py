"""
Unit tests for schema normalization, validation, header matching, and rejection rules.
"""
import pytest
from solution.schema import (
    SchemaRegistry,
    normalize_header,
    parse_date,
    parse_decimal,
    parse_enum,
    parse_phone,
    parse_timestamp_ist,
)


def test_normalize_header():
    assert normalize_header("Mobile No") == "mobile no"
    assert normalize_header("Customer_Name") == "customer name"
    assert normalize_header("  policy__number  ") == "policy number"
    assert normalize_header("expiry   date") == "expiry date"
    assert normalize_header("") == ""


def test_parse_phone_valid_cases():
    # 10 digits starting with 6, 7, 8, 9
    assert parse_phone("9876543210") == "+919876543210"
    assert parse_phone("8880395512") == "+918880395512"
    assert parse_phone("7136363179") == "+917136363179"
    assert parse_phone("6271122182") == "+916271122182"
    # With +91
    assert parse_phone("+919876543210") == "+919876543210"
    assert parse_phone("+91 79328 96340") == "+917932896340"
    # With 91- prefix
    assert parse_phone("91-81518-64492") == "+918151864492"
    # With 0 prefix (11 digits)
    assert parse_phone("06832582386") == "+916832582386"
    assert parse_phone("09876543210") == "+919876543210"
    # With (+91)
    assert parse_phone("(+91) 8196106462") == "+918196106462"


def test_parse_phone_invalid_cases():
    # Less than 10 digits
    assert parse_phone("987654321") is None
    # 10 digits starting with invalid digit (1-5)
    assert parse_phone("5123456789") is None
    assert parse_phone("1234567890") is None
    # Landlines / International numbers
    assert parse_phone("022-2451-1234") is None
    assert parse_phone("+1 415 555 0134") is None
    assert parse_phone("N/A") is None
    assert parse_phone("") is None
    assert parse_phone(None) is None


def test_parse_date_valid_cases():
    assert parse_date("2026-11-23") == "2026-11-23"
    assert parse_date("27/10/2026") == "2026-10-27"
    assert parse_date("21-11-2026") == "2026-11-21"
    assert parse_date("29-Dec-2026") == "2026-12-29"
    assert parse_date("01-dec-2026") == "2026-12-01"
    assert parse_date("15-AUG-2026") == "2026-08-15"


def test_parse_date_invalid_cases():
    assert parse_date("31/02/2026") is None  # impossible date
    assert parse_date("2026-13-01") is None  # month 13
    assert parse_date("soon") is None
    assert parse_date("TBD") is None
    assert parse_date("") is None
    assert parse_date(None) is None


def test_parse_decimal_valid_cases():
    assert parse_decimal("6850") == 6850.0
    assert parse_decimal("4985.75") == 4985.75
    assert parse_decimal("₹27,554") == 27554.0
    assert parse_decimal("₹ 10,464") == 10464.0
    assert parse_decimal("Rs. 31629") == 31629.0
    assert parse_decimal("rs 500") == 500.0
    assert parse_decimal("INR 25301.4") == 25301.4
    assert parse_decimal("INR 15176.556") == 15176.56  # rounded to 2 places
    assert parse_decimal("0") == 0.0


def test_parse_decimal_invalid_cases():
    assert parse_decimal("-") is None
    assert parse_decimal("N/A") is None
    assert parse_decimal("twelve thousand") is None
    assert parse_decimal("") is None
    assert parse_decimal(None) is None


def test_parse_enum_cases():
    allowed = ("en", "hi", "ta", "kn", "te", "mr")
    assert parse_enum("en", allowed) == "en"
    assert parse_enum("HI", allowed) == "hi"
    assert parse_enum("  Ta  ", allowed) == "ta"
    assert parse_enum("Hindi", allowed) is None
    assert parse_enum("english", allowed) is None
    assert parse_enum("", allowed) is None
    assert parse_enum(None, allowed) is None


def test_parse_timestamp_ist():
    # UTC
    ts_utc = parse_timestamp_ist("2026-10-05T13:20:21Z")
    assert ts_utc is not None
    assert ts_utc.hour == 18 and ts_utc.minute == 50 and ts_utc.second == 21  # 13:20:21 UTC = 18:50:21 IST

    # Offset
    ts_off = parse_timestamp_ist("2026-10-01T10:40:19+05:30")
    assert ts_off is not None
    assert ts_off.hour == 10 and ts_off.minute == 40

    # Naive means IST
    ts_naive = parse_timestamp_ist("2026-10-01 09:15:55")
    assert ts_naive is not None
    assert ts_naive.hour == 9 and ts_naive.minute == 15

