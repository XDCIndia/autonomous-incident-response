"""User accounts, per-user ownership, audit log, and RBAC (Phase 6).

Extends the Phase 5 shared-password login into a proper user-account
system with role-based access control.

Roles:
- viewer: can view incidents and targets, cannot modify
- operator: can create/delete targets, trigger incidents, approve/reject
- admin: full access including user management

Every target is owned by the user who created it. Only the owner or
an admin can modify/delete a target. Approval of SEMI_AUTONOMOUS
remediations requires operator or admin role.

All mutations are logged to an audit trail for accountability.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class UserRole(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class User(BaseModel):
    """A user account in the system."""
    id: str = Field(default_factory=lambda: secrets.token_urlsafe(16))
    username: str
    password_hash: str  # bcrypt or argon2 hash
    role: UserRole = UserRole.VIEWER
    display_name: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login: Optional[datetime] = None
    is_active: bool = True


class AuditEntry(BaseModel):
    """An audit log entry for accountability."""
    id: str = Field(default_factory=lambda: secrets.token_urlsafe(16))
    user_id: str
    username: str
    action: str  # "target.create", "target.delete", "incident.approve", etc.
    resource_type: str  # "target", "incident", "user"
    resource_id: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Password hashing (simple SHA-256 with salt for demo — use bcrypt/argon2 in production)
# ---------------------------------------------------------------------------


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Hash a password with a random salt.

    Returns "salt:hash" format. In production, use bcrypt or argon2.
    """
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash."""
    salt, expected_hash = stored.split(":", 1)
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return h == expected_hash


# ---------------------------------------------------------------------------
# RBAC permission checks
# ---------------------------------------------------------------------------


def can_view(user_role: UserRole) -> bool:
    """All authenticated users can view."""
    return True


def can_create_target(user_role: UserRole) -> bool:
    """Operators and admins can create targets."""
    return user_role in (UserRole.OPERATOR, UserRole.ADMIN)


def can_delete_target(user_role: UserRole) -> bool:
    """Operators and admins can delete targets."""
    return user_role in (UserRole.OPERATOR, UserRole.ADMIN)


def can_toggle_monitoring(user_role: UserRole) -> bool:
    """Operators and admins can toggle monitoring."""
    return user_role in (UserRole.OPERATOR, UserRole.ADMIN)


def can_trigger_incident(user_role: UserRole) -> bool:
    """Only admins can trigger incidents (simulator)."""
    return user_role == UserRole.ADMIN


def can_approve_remediation(user_role: UserRole) -> bool:
    """Operators and admins can approve SEMI_AUTONOMOUS remediations."""
    return user_role in (UserRole.OPERATOR, UserRole.ADMIN)


def can_manage_users(user_role: UserRole) -> bool:
    """Only admins can manage users."""
    return user_role == UserRole.ADMIN


def can_inject_fault(user_role: UserRole) -> bool:
    """Only admins can inject faults (simulator)."""
    return user_role == UserRole.ADMIN


def can_execute_remediation(user_role: UserRole) -> bool:
    """Only admins can execute remediation (simulator)."""
    return user_role == UserRole.ADMIN


def owns_target(user: User, target_owner_id: Optional[str]) -> bool:
    """Check if a user owns a target. Admins own everything."""
    if user.role == UserRole.ADMIN:
        return True
    return target_owner_id == user.id
