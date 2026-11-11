from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.common import load_env


EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?57[\s.-]?)?(?:3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}|\d{7,10})(?!\w)"
)
IDENTITY_RE = re.compile(
    r"(?<!\w)(?:\d{6,10}|\d{1,3}(?:[.\s]\d{3}){2,3})(?:-\d)?(?!\w)"
)
PII_FIELDS = (
    "razon_social",
    "nit",
    "email_contacto",
    "celular_contacto",
    "direccion",
    "representante_legal",
    "observaciones",
)


def encryption_key() -> bytes:
    load_env()
    encoded = os.getenv("APP_ENCRYPTION_KEY", "")
    if not encoded:
        raise RuntimeError("APP_ENCRYPTION_KEY is missing from the local environment")
    try:
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("APP_ENCRYPTION_KEY must be URL-safe base64") from exc
    if len(key) != 32:
        raise RuntimeError("APP_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def encrypt_text(value: str, key: bytes, associated_data: bytes) -> bytes:
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=None,
        info=b"bia-data-platform-client-pii-v1",
    ).derive(key)
    encryption_key, nonce_key = derived[:32], derived[32:]
    plain = value.encode("utf-8")
    nonce = hmac.new(nonce_key, associated_data + plain, hashlib.sha256).digest()[:12]
    return b"\x01" + nonce + AESGCM(encryption_key).encrypt(
        nonce, plain, associated_data
    )


def mask_free_text(value: str) -> tuple[str, bool]:
    found = bool(EMAIL_RE.search(value) or PHONE_RE.search(value) or IDENTITY_RE.search(value))
    masked = EMAIL_RE.sub("[EMAIL_ENMASCARADO]", value)
    masked = PHONE_RE.sub("[TELEFONO_ENMASCARADO]", masked)
    masked = IDENTITY_RE.sub("[ID_ENMASCARADO]", masked)
    return masked, found


def protect_client(row: dict[str, str], key: bytes) -> tuple[dict[str, object], bool]:
    observation, has_pii = mask_free_text(row.get("observaciones", ""))
    plain = {**row, "observaciones": observation}
    protected: dict[str, object] = {"cliente_id": row["cliente_id"]}
    protected["ciudad"] = row.get("ciudad", "")
    protected["departamento"] = row.get("departamento", "")
    for field in PII_FIELDS:
        protected[field] = encrypt_text(
            plain.get(field, ""),
            key,
            f"core.dim_cliente:{row['cliente_id']}:{field}".encode("utf-8"),
        )
    return protected, has_pii
