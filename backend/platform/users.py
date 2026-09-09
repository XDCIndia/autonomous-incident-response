"""User accounts for dashboard login (multi-user auth).

Lives in its own table in the same SQLite file ``storage.py`` already owns,
so there's exactly one database file for the whole app (same pattern as
``knowledge_base.py``).

Passwords are hashed with PBKDF2-HMAC-SHA256 from the Python standard
library — a NIST-approved KDF, available with zero new dependencies.
Each password gets its own random 128-bit salt and is verified with a
constant-time comparison. The stored format is self-describing:

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

No plaintext password ever touches the database, and the hash is never
returned by any API response (see backend.api.app's UserIdentity model).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from backend.platform.storage import Storage

logger = logging.getLogger(__name__)

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16
_DKLEN = 32

# Pragmatic demo-grade validation: something@something.tld, no spaces.
# (EmailStr from pydantic needs `email-validator`, which is not in the
# dependency list — this regex keeps us dependency-free on purpose.)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

PASSWORD_MIN_LENGTH = 8


class DuplicateEmailError(Exception):
    """Signup attempted with an email that already exists."""


class User(BaseModel):
    """A user account as stored — includes the hash, so NEVER serialize
    this directly into an API response."""

    id: str
    name: str
    email: str
    password_hash: str
    created_at: str


class UserIdentity(BaseModel):
    """The safe, API-facing view of a user — no password_hash."""

    name: str
    email: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS, dklen=_DKLEN)
    return f"{_ALGORITHM}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification; any malformed stored value fails closed."""
    try:
        algorithm, iterations, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        iterations_i = int(iterations)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations_i, dklen=len(expected))
    return hmac.compare_digest(digest, expected)


async def create_user(storage: Storage, name: str, email: str, password: str) -> User:
    """Create a user. Raises DuplicateEmailError when the email is taken."""
    normalized = normalize_email(email)
    existing = await get_user_by_email(storage, normalized)
    if existing is not None:
        raise DuplicateEmailError(normalized)
    user = User(
        id=str(uuid.uuid4()),
        name=name.strip(),
        email=normalized,
        password_hash=hash_password(password),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    conn = storage._require_conn()
    await conn.execute(
        """
        INSERT INTO users (id, name, email, password_hash, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user.id, user.name, user.email, user.password_hash, user.created_at),
    )
    await conn.commit()
    logger.info("User created: %s <%s>", user.name, user.email)
    return user


async def get_user_by_email(storage: Storage, email: str) -> User | None:
    row = await _fetch_one(
        storage, "SELECT id, name, email, password_hash, created_at FROM users WHERE email = ?",
        (normalize_email(email),),
    )
    return _row_to_user(row)


async def get_user_by_id(storage: Storage, user_id: str) -> User | None:
    row = await _fetch_one(
        storage, "SELECT id, name, email, password_hash, created_at FROM users WHERE id = ?",
        (user_id,),
    )
    return _row_to_user(row)


async def count_users(storage: Storage) -> int:
    row = await _fetch_one(storage, "SELECT COUNT(*) AS n FROM users", ())
    return int(row["n"]) if row else 0


async def seed_demo_user(storage: Storage, name: str, email: str, password: str) -> User | None:
    """Create the configured demo account if it doesn't exist yet.

    Called from the app lifespan — the normal startup/seed mechanism, so a
    fresh deployment has a deterministic account without any manual SQL.
    Credentials come exclusively from the environment (DEMO_USER_*), never
    from source code. Returns None when already present.
    """
    if not email.strip() or not password:
        return None
    existing = await get_user_by_email(storage, email)
    if existing is not None:
        return None
    try:
        return await create_user(storage, name.strip() or "Demo Operator", email, password)
    except DuplicateEmailError:  # race-safe
        return None


async def _fetch_one(storage: Storage, sql: str, params: tuple) -> Any | None:
    conn = storage._require_conn()
    async with conn.execute(sql, params) as cursor:
        return await cursor.fetchone()


def _row_to_user(row: Any) -> User | None:
    if row is None:
        return None
    return User(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
    )
