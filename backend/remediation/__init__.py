"""Remediation module — fixed action interface for incident remediation.

NEVER allow an LLM to execute arbitrary shell commands or code.
All actions are from a fixed, allowed list.
"""

from backend.remediation.actions import (
    AllowedAction,
    RemediationEngine,
)

__all__ = [
    "AllowedAction",
    "RemediationEngine",
]
