"""
Cryptographic module for PII protection at rest.

Guarantees:
1. PII (phone numbers, customer names) is never stored in plaintext in the database.
2. Searchable Blind Index: Exact matching of phone numbers between leads and events
   is performed via deterministic HMAC-SHA256 tokens ('phone_hash').
3. Authenticated Encryption at Rest (AEAD): Plaintext values are encrypted with a random
   nonce, CTR keystream generated via HMAC-SHA256 PRF, and authenticated with an HMAC tag (Encrypt-then-MAC).
4. Phone Masking: Produces '+91******' followed by the last 4 digits for reports.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
from solution.config import PII_SECRET_KEY


def blind_index_phone(canonical_phone: str, key: bytes | None = None) -> str:
    """
    Computes a deterministic blind index for a canonical phone number (+91XXXXXXXXXX)
    using HMAC-SHA256. This allows exact-match lookups without storing plaintext phone numbers.
    """
    secret = key or PII_SECRET_KEY
    h = hmac.new(secret, canonical_phone.encode("utf-8"), hashlib.sha256)
    return h.hexdigest()


def mask_phone(canonical_phone: str) -> str:
    """
    Masks a canonical phone number per spec: '+91******' followed by the last 4 digits.
    Example: '+919876543210' -> '+91******3210'
    """
    if not canonical_phone or len(canonical_phone) < 4:
        return ""
    last_four = canonical_phone[-4:]
    return f"+91******{last_four}"


def _derive_keys(master_key: bytes) -> tuple[bytes, bytes]:
    """Derives separate encryption (enc_key) and authentication (mac_key) keys."""
    enc_key = hashlib.sha256(master_key + b"::ENC").digest()
    mac_key = hashlib.sha256(master_key + b"::MAC").digest()
    return enc_key, mac_key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """Generates a pseudo-random keystream using HMAC-SHA256 in counter mode."""
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        block = hmac.new(key, nonce + struct.pack(">Q", counter), hashlib.sha256).digest()
        stream.extend(block)
        counter += 1
    return bytes(stream[:length])


def encrypt_pii(plaintext: str, key: bytes | None = None) -> str:
    """
    Encrypts sensitive PII plaintext using Encrypt-then-MAC (HMAC-SHA256 PRF CTR + HMAC-SHA256).
    Output format: base64( nonce[16] || hmac_tag[32] || ciphertext )
    """
    if plaintext is None:
        return ""
    master = key or PII_SECRET_KEY
    enc_key, mac_key = _derive_keys(master)
    
    data = plaintext.encode("utf-8")
    nonce = os.urandom(16)
    ks = _keystream(enc_key, nonce, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, ks))
    
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    payload = nonce + tag + ciphertext
    return base64.b64encode(payload).decode("ascii")


def decrypt_pii(token_b64: str, key: bytes | None = None) -> str:
    """
    Decrypts an encrypted PII token and verifies its authenticity.
    Raises ValueError if the tag is invalid or payload is corrupted.
    """
    if not token_b64:
        return ""
    master = key or PII_SECRET_KEY
    enc_key, mac_key = _derive_keys(master)
    
    try:
        payload = base64.b64decode(token_b64.encode("ascii"))
    except Exception as e:
        raise ValueError(f"Corrupted base64 ciphertext: {e}") from e
    
    if len(payload) < 48:
        raise ValueError("Ciphertext payload too short")
    
    nonce = payload[:16]
    tag = payload[16:48]
    ciphertext = payload[48:]
    
    expected_tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("PII decryption failed: HMAC tag mismatch (tampering detected)")
    
    ks = _keystream(enc_key, nonce, len(ciphertext))
    decrypted_bytes = bytes(a ^ b for a, b in zip(ciphertext, ks))
    return decrypted_bytes.decode("utf-8")

