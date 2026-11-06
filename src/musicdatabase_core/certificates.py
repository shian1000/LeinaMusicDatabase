"""The code that identifies a sharing server's own certificate."""

import base64
import hashlib


def certificate_code(der: bytes) -> str:
    """The short code the owner compares in Leina Radio: the certificate's
    SHA-256 in base32, first 12 characters, "XXXX-XXXX-XXXX"."""
    code = base64.b32encode(hashlib.sha256(der).digest()).decode()[:12]
    return f"{code[:4]}-{code[4:8]}-{code[8:]}"
