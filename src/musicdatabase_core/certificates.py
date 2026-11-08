"""The code that identifies a sharing server's own certificate."""

import base64
import hashlib
import os
import ssl
import subprocess

TLS_CERT_FILE = "cert.pem"
TLS_KEY_FILE = "key.pem"
TLS_CERT_DAYS = 3650


def certificate_code(der: bytes) -> str:
    """The short code the owner compares in Leina Radio: the certificate's
    SHA-256 in base32, first 12 characters, "XXXX-XXXX-XXXX"."""
    code = base64.b32encode(hashlib.sha256(der).digest()).decode()[:12]
    return f"{code[:4]}-{code[4:8]}-{code[8:]}"


def tls_certificate_code(tls_dir: str) -> str | None:
    """certificate_code() of the certificate in tls_dir, None when there's none (yet)."""
    try:
        with open(os.path.join(tls_dir, TLS_CERT_FILE), encoding="ascii") as file:
            return certificate_code(ssl.PEM_cert_to_DER_cert(file.read()))
    except (OSError, ValueError):
        return None


def ensure_tls_certificate(tls_dir: str, run=subprocess.run) -> None:
    """Makes the server's own certificate and key in tls_dir unless both are
    there. The folder and the key are this user's only."""
    cert = os.path.join(tls_dir, TLS_CERT_FILE)
    key = os.path.join(tls_dir, TLS_KEY_FILE)
    if os.path.exists(cert) and os.path.exists(key):
        return
    os.makedirs(tls_dir, mode=0o700, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
             "-nodes", "-days", str(TLS_CERT_DAYS), "-subj", "/CN=MusicDatabase sharing server",
             "-keyout", key, "-out", cert], check=True, capture_output=True, timeout=60)
    finally:
        os.umask(old_umask)
