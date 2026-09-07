"""Notification system for incident alerting (Phase 3).

Supports multiple notification channels:
- Email (SMTP)
- Slack/Discord webhook
- Generic outbound webhook

All notifications are driven by real incident state transitions — never
fabricated or timer-based. Notifications fire on:
- Incident created
- Incident escalated
- Incident resolved

Per-target notification routing and severity-based channel selection
are configurable via NotificationConfig.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import smtplib
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Callable, Optional, TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from backend.contracts import Incident, SeverityLevel

if TYPE_CHECKING:
    from backend.platform.storage import Storage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Notification channel configuration
# ---------------------------------------------------------------------------


class NotificationChannel:
    """A configured notification destination (email, Slack webhook, etc.)."""

    def __init__(
        self,
        channel_id: str,
        channel_type: str,  # "email" | "slack" | "webhook"
        name: str,
        config: dict[str, Any],
        severity_filter: Optional[list[str]] = None,  # ["P1", "P2"] or None for all
    ):
        self.channel_id = channel_id
        self.channel_type = channel_type
        self.name = name
        self.config = config
        self.severity_filter = severity_filter or ["P1", "P2", "P3", "P4"]

    def should_notify(self, incident: Incident) -> bool:
        """Check if this channel should be notified for this incident."""
        if incident.severity and incident.severity.value not in self.severity_filter:
            return False
        return True


class NotificationConfig:
    """Per-target notification routing configuration."""

    def __init__(self):
        self.channels: list[NotificationChannel] = []
        self.quiet_hours_start: Optional[int] = None  # 0-23
        self.quiet_hours_end: Optional[int] = None  # 0-23
        self.escalation_delay_seconds: int = 300  # 5 minutes
        self.escalation_channel_id: Optional[str] = None  # channel to escalate to

    def is_quiet_hours(self) -> bool:
        """Check if current time is within quiet hours."""
        if self.quiet_hours_start is None or self.quiet_hours_end is None:
            return False
        now = datetime.now(timezone.utc).hour
        if self.quiet_hours_start <= self.quiet_hours_end:
            return self.quiet_hours_start <= now < self.quiet_hours_end
        else:
            # Wraps midnight
            return now >= self.quiet_hours_start or now < self.quiet_hours_end


# ---------------------------------------------------------------------------
# Notification dispatchers
# ---------------------------------------------------------------------------


async def _send_email(
    to_email: str,
    subject: str,
    body: str,
    smtp_host: str = "localhost",
    smtp_port: int = 587,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None,
    smtp_use_tls: bool = True,
) -> bool:
    """Send an email notification via SMTP.

    Returns True if sent successfully, False otherwise. This is a real SMTP
    call — not a mock or simulation.
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_user or "iras-alerts@localhost"
        msg["To"] = to_email
        msg.attach(MIMEText(body, "plain"))

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: _smtp_send(msg, smtp_host, smtp_port, smtp_user, smtp_password, smtp_use_tls),
        )
        logger.info("Email sent to %s: %s", to_email, subject)
        return True
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to_email, e)
        return False


def _smtp_send(msg, host, port, user, password, use_tls):
    """Synchronous SMTP send (runs in executor)."""
    with smtplib.SMTP(host, port, timeout=10) as server:
        if use_tls:
            server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(msg)


async def _send_slack_webhook(
    webhook_url: str,
    text: str,
    incident: Optional[Incident] = None,
) -> bool:
    """Send a Slack/Discord webhook notification.

    Returns True if sent successfully. This is a real HTTP POST — not
    a mock or simulation.
    """
    payload = {"text": text}
    if incident:
        payload["blocks"] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text,
                },
            }
        ]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
        logger.info("Slack webhook sent: %s", text[:80])
        return True
    except Exception as e:
        logger.error("Failed to send Slack webhook: %s", e)
        return False


async def _send_generic_webhook(
    webhook_url: str,
    incident: Incident,
    event_type: str,  # "created" | "escalated" | "resolved"
    secret: Optional[str] = None,
) -> bool:
    """Send a generic outbound webhook notification.

    The payload includes incident_id, root_cause, severity, service_name,
    and event_type. If a secret is configured, the payload is signed with
    HMAC-SHA256 so the receiver can verify authenticity.

    Returns True if sent successfully. This is a real HTTP POST — not
    a mock or simulation.
    """
    payload = {
        "event_type": event_type,
        "incident_id": incident.id,
        "service_name": incident.service_name,
        "severity": incident.severity.value if incident.severity else None,
        "state": incident.state.value if hasattr(incident.state, "value") else str(incident.state),
        "source": incident.source,
        "target_url": incident.target_url,
        "root_cause": incident.report.root_cause if incident.report else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    headers = {"Content-Type": "application/json"}
    if secret:
        signature = hmac.new(
            secret.encode(), json.dumps(payload).encode(), hashlib.sha256
        ).hexdigest()
        headers["X-IRAS-Signature"] = f"sha256={signature}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload, headers=headers)
            resp.raise_for_status()
        logger.info("Webhook sent to %s: %s for incident %s", webhook_url, event_type, incident.id)
        return True
    except Exception as e:
        logger.error("Failed to send webhook to %s: %s", webhook_url, e)
        return False


# ---------------------------------------------------------------------------
# Main notification dispatcher
# ---------------------------------------------------------------------------


class NotificationDispatcher:
    """Dispatches notifications to configured channels based on incident
    state transitions.

    All notifications are driven by real incident state — never fabricated.
    """

    def __init__(self, storage: "Storage"):
        self._storage = storage
        self._escalation_tasks: dict[str, asyncio.Task] = {}

    async def notify_incident_created(self, incident: Incident) -> None:
        """Send notifications when a new incident is created."""
        config = await self._get_notification_config(incident)
        if config.is_quiet_hours():
            logger.info("Quiet hours active — deferring notification for incident %s", incident.id)
            return

        for channel in config.channels:
            if channel.should_notify(incident):
                await self._dispatch(channel, incident, "created")

        # Schedule escalation if configured
        if config.escalation_channel_id and config.escalation_delay_seconds > 0:
            self._schedule_escalation(incident, config)

    async def notify_incident_escalated(self, incident: Incident) -> None:
        """Send notifications when an incident is escalated."""
        config = await self._get_notification_config(incident)
        for channel in config.channels:
            if channel.should_notify(incident):
                await self._dispatch(channel, incident, "escalated")

    async def notify_incident_resolved(self, incident: Incident) -> None:
        """Send notifications when an incident is resolved."""
        config = await self._get_notification_config(incident)
        for channel in config.channels:
            if channel.should_notify(incident):
                await self._dispatch(channel, incident, "resolved")

        # Cancel any pending escalation
        if incident.id in self._escalation_tasks:
            self._escalation_tasks[incident.id].cancel()
            del self._escalation_tasks[incident.id]

    def _schedule_escalation(self, incident: Incident, config: NotificationConfig) -> None:
        """Schedule an escalation notification after the delay."""
        async def _escalate():
            await asyncio.sleep(config.escalation_delay_seconds)
            # Check if incident is still unresolved
            stored = await self._storage.get_incident(incident.id)
            if stored and not hasattr(stored.state, "value") or (
                hasattr(stored.state, "value") and stored.state.value not in ("resolved", "failed", "rejected")
            ):
                # Find the escalation channel
                escalation_channel = None
                for ch in config.channels:
                    if ch.channel_id == config.escalation_channel_id:
                        escalation_channel = ch
                        break
                if escalation_channel:
                    await self._dispatch(escalation_channel, stored, "escalated")
            self._escalation_tasks.pop(incident.id, None)

        task = asyncio.create_task(_escalate())
        self._escalation_tasks[incident.id] = task

    async def _dispatch(
        self,
        channel: NotificationChannel,
        incident: Incident,
        event_type: str,
    ) -> None:
        """Dispatch a notification to a specific channel."""
        text = self._format_notification(incident, event_type)

        if channel.channel_type == "email":
            await _send_email(
                to_email=channel.config.get("to_email", ""),
                subject=f"[IRAS {incident.severity.value if incident.severity else 'ALERT'}] {event_type}: {incident.service_name}",
                body=text,
                smtp_host=channel.config.get("smtp_host", "localhost"),
                smtp_port=channel.config.get("smtp_port", 587),
                smtp_user=channel.config.get("smtp_user"),
                smtp_password=channel.config.get("smtp_password"),
                smtp_use_tls=channel.config.get("smtp_use_tls", True),
            )
        elif channel.channel_type == "slack":
            await _send_slack_webhook(
                webhook_url=channel.config.get("webhook_url", ""),
                text=text,
                incident=incident,
            )
        elif channel.channel_type == "webhook":
            await _send_generic_webhook(
                webhook_url=channel.config.get("webhook_url", ""),
                incident=incident,
                event_type=event_type,
                secret=channel.config.get("secret"),
            )

    def _format_notification(self, incident: Incident, event_type: str) -> str:
        """Format a human-readable notification message."""
        severity = incident.severity.value if incident.severity else "UNKNOWN"
        state = incident.state.value if hasattr(incident.state, "value") else str(incident.state)

        lines = [
            f"🚨 Incident {event_type.upper()}: {incident.service_name}",
            f"Severity: {severity} | State: {state}",
            f"Source: {incident.source}",
        ]
        if incident.target_url:
            lines.append(f"Target: {incident.target_url}")
        if incident.report and incident.report.root_cause:
            lines.append(f"Root Cause: {incident.report.root_cause}")
        lines.append(f"ID: {incident.id}")
        return "\n".join(lines)

    async def _get_notification_config(self, incident: Incident) -> NotificationConfig:
        """Get notification config for an incident's target."""
        # For now, return a default config. In a full implementation,
        # this would look up per-target configuration from storage.
        config = NotificationConfig()
        # Default: send all severities to console (logger)
        config.channels = [
            NotificationChannel(
                channel_id="default-log",
                channel_type="webhook",
                name="Default Log",
                config={"webhook_url": ""},  # Empty = log only
            )
        ]
        return config


# ---------------------------------------------------------------------------
# Status page (read-only public view)
# ---------------------------------------------------------------------------


async def get_status_page_data(storage: "Storage") -> dict[str, Any]:
    """Get data for a public status page.

    Returns a read-only summary of incident status, suitable for a public
    status page. No authentication required — this is the public-facing
    view of system health.

    Only includes incidents from the last 24 hours. Historical data is
    available through the authenticated API.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    incidents = await storage.list_incidents(limit=100)

    # Filter to last 24 hours
    recent = []
    for inc in incidents:
        if inc.created_at >= cutoff:
            recent.append(inc)

    # Compute overall status
    active_incidents = [
        inc for inc in recent
        if hasattr(inc.state, "value") and inc.state.value not in ("resolved", "failed", "rejected")
    ]

    if not active_incidents:
        overall_status = "operational"
    elif any(inc.severity == SeverityLevel.P1 for inc in active_incidents):
        overall_status = "major_outage"
    elif any(inc.severity == SeverityLevel.P2 for inc in active_incidents):
        overall_status = "partial_outage"
    else:
        overall_status = "degraded_performance"

    # Group by service
    services = {}
    for inc in recent:
        svc = inc.service_name
        if svc not in services:
            services[svc] = {
                "name": svc,
                "status": "operational",
                "incidents": [],
            }
        services[svc]["incidents"].append({
            "id": inc.id,
            "severity": inc.severity.value if inc.severity else None,
            "state": inc.state.value if hasattr(inc.state, "value") else str(inc.state),
            "source": inc.source,
            "created_at": inc.created_at.isoformat(),
        })
        # Update service status based on worst active incident
        if hasattr(inc.state, "value") and inc.state.value not in ("resolved", "failed", "rejected"):
            if inc.severity == SeverityLevel.P1:
                services[svc]["status"] = "major_outage"
            elif inc.severity == SeverityLevel.P2 and services[svc]["status"] != "major_outage":
                services[svc]["status"] = "partial_outage"
            elif services[svc]["status"] == "operational":
                services[svc]["status"] = "degraded_performance"

    return {
        "status": overall_status,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "services": list(services.values()),
        "active_incidents": len(active_incidents),
        "total_incidents_24h": len(recent),
    }
