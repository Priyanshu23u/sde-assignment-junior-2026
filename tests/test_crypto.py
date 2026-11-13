"""
Unit tests for cryptographic operations and PII protection.
"""
import pytest
from solution.crypto import (
    blind_index_phone,
    decrypt_pii,
    encrypt_pii,
    mask_phone,
)


def test_blind_index_deterministic():
    phone = "+919876543210"
    idx1 = blind_index_phone(phone)
    idx2 = blind_index_phone(phone)
    assert idx1 == idx2
    assert len(idx1) == 64  # SHA-256 hex string


def test_blind_index_different_for_different_phones():
    p1 = "+919876543210"
    p2 = "+919876543211"
    assert blind_index_phone(p1) != blind_index_phone(p2)


def test_mask_phone():
    assert mask_phone("+919876543210") == "+91******3210"
    assert mask_phone("+917718750036") == "+91******0036"
    assert mask_phone("") == ""
    assert mask_phone("123") == ""


def test_aead_encrypt_decrypt_roundtrip():
    test_strings = [
        "Aarav Sharma",
        "+919876543210",
        "Pooja Iyer",
        "Special chars: !@#$%^&*()_+~`|}{[]:;?><,./",
        "Unicode: प्रियदर्शी शर्मा 123",
    ]
    for s in test_strings:
        enc = encrypt_pii(s)
        assert enc != s
        dec = decrypt_pii(enc)
        assert dec == s


def test_aead_tamper_detection():
    enc = encrypt_pii("Sensitive Customer Data")
    # Mutate a character in the ciphertext
    tampered_bytes = bytearray(enc, "ascii")
    tampered_bytes[-2] = ord("A") if tampered_bytes[-2] != ord("A") else ord("B")
    tampered = tampered_bytes.decode("ascii")

    with pytest.raises(ValueError, match="PII decryption failed|Corrupted base64"):
        decrypt_pii(tampered)


def test_encrypt_empty():
    assert encrypt_pii("") != ""
    assert decrypt_pii(encrypt_pii("")) == ""
    assert decrypt_pii("") == ""


def test_zero_plaintext_pii_in_database(tmp_path):
    from solution.database import get_connection, init_db
    from solution.ingest_leads import ingest_lead_file
    from solution.ingest_events import ingest_event_file
    from solution.schema import SchemaRegistry

    db_path = tmp_path / "zero_pii.db"
    init_db(db_path)
    conn = get_connection(db_path)
    reg = SchemaRegistry()

    # Ingest lead
    raw_name = "Aarav Sharma"
    raw_phone = "+919876543210"
    csv_file = tmp_path / "batch.csv"
    csv_file.write_text(
        f"customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        f"{raw_name},{raw_phone},MOT/2026/999999,2026-12-01,5000,hi\n",
        encoding="utf-8",
    )
    ingest_lead_file(conn, csv_file, "motor_renewal", reg, "drop_1/leads/motor_renewal/batch.csv")

    # Ingest event
    jsonl_file = tmp_path / "event.jsonl"
    jsonl_file.write_text(
        f'{{"event_id": "evt_pii_1", "event_type": "call.initiated", "workflow_id": "motor_renewal", '
        f'"call_id": "call_pii_1", "occurred_at": "2026-10-01T10:00:00Z", "phone": "{raw_phone}", "payload": {{}}}}\n',
        encoding="utf-8",
    )
    ingest_event_file(conn, jsonl_file, reg, "drop_1/events/event.jsonl")

    # Assert no plaintext customer_name or raw_phone appears anywhere in database
    for table in ["leads", "events", "rejections"]:
        cursor = conn.execute(f"SELECT * FROM {table};")
        for row in cursor.fetchall():
            for col_idx, val in enumerate(row):
                val_str = str(val)
                assert raw_name not in val_str, f"Plaintext customer name leaked in table '{table}' column {col_idx}: {val_str}"
                assert "9876543210" not in val_str or "+91******3210" in val_str, (
                    f"Plaintext phone number leaked in table '{table}' column {col_idx}: {val_str}"
                )

    # Verify decrypt_pii successfully restores the plaintext
    lead_row = conn.execute("SELECT * FROM leads;").fetchone()
    assert decrypt_pii(lead_row["name_encrypted"]) == raw_name
    assert decrypt_pii(lead_row["phone_encrypted"]) == raw_phone
    assert lead_row["phone_masked"] == "+91******3210"

    conn.close()

