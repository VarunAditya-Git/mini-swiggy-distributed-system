"""
server/auth.py
==============
Cryptographic security and authentication primitives for Mini Swiggy V4.1.

Security specifications:
- Password hashing: hashlib.scrypt with standard memory-hard work factors
  (N=16384, r=8, p=1) and 16-byte cryptographically secure random salt.
- Constant-time password and token verification (hmac.compare_digest).
- Token generation using Python's `secrets` module.
- Strict token hashing: tokens are stored in the database ONLY as SHA-256 hashes.
- Sensible password strength validation and email format verification.
"""

import hashlib
import hmac
import re
import secrets
from typing import Tuple

# OWASP Recommended standard scrypt parameters
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT_BYTES = 16
SCRYPT_KEY_LEN = 64

# RFC 5322 simplified email validation regex
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+$"
)


def hash_password(password: str) -> str:
    """
    Hash a plaintext password using hashlib.scrypt with a unique random salt.

    Returns:
        Formatted string: 'scrypt$16384$8$1$<salt_hex>$<key_hex>'
    """
    if not isinstance(password, str):
        raise TypeError("Password must be a string.")
    
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=0,
        dklen=SCRYPT_KEY_LEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    """
    Verify a plaintext password against a stored scrypt hash using constant-time comparison.
    """
    if not password or not stored_hash or not isinstance(stored_hash, str):
        return False

    parts = stored_hash.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False

    try:
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt = bytes.fromhex(parts[4])
        expected_key = bytes.fromhex(parts[5])

        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=0,
            dklen=len(expected_key),
        )
        return hmac.compare_digest(derived, expected_key)
    except Exception:
        return False


def validate_password(password: str) -> Tuple[bool, str]:
    """
    Enforce sensible password requirements:
    - Minimum 8 characters.
    - At least one letter.
    - At least one digit.
    """
    if not password or not isinstance(password, str):
        return False, "Password is required."
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(c.isalpha() for c in password):
        return False, "Password must contain at least one letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number."
    return True, ""


def validate_email(email: str) -> bool:
    """Validate standard email address syntax."""
    if not email or not isinstance(email, str):
        return False
    email = email.strip()
    if len(email) > 254:
        return False
    return bool(EMAIL_REGEX.match(email))


def generate_token() -> Tuple[str, str]:
    """
    Generate a cryptographically secure random token and its SHA-256 database hash.

    Returns:
        (raw_token, token_hash)
        - raw_token is provided to the client / sent in email.
        - token_hash is stored in the database.
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_token(raw_token)
    return raw_token, token_hash


def hash_token(raw_token: str) -> str:
    """Compute the SHA-256 hex digest of a raw token."""
    if not raw_token or not isinstance(raw_token, str):
        return ""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
