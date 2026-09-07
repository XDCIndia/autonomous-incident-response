"""Generic webhook/runbook remediation action (Phase 4).

Instead of requiring a DockerController, lets a target register a URL that
IRAS calls with a signed payload when remediation is needed. The user's
own automation (Lambda, GitHub Action, K8s operator) does the actual fix
and reports back success/failure.

This preserves the "fixed allowed action set, no arbitrary code execution"
safety invariant while extending remediation reach past Docker.

The existing Docker/Toxiproxy path is unchanged — it remains the
remediation path for simulator incidents.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from backend.contracts import Incident, RemediationResult

logger = logging.getLogger(__name__)

# Webhook remediation timeout — how long we wait for the remote system
# to acknowledge and report back.
WEBHOOK_TIMEOUT_SECONDS = 30.0


class WebhookRemediation:
    """Calls a user-registered webhook URL with a signed remediation request.

    The payload includes:
    - incident_id: the incident that triggered remediation
    - root_cause: the determined root cause (from the arbiter)
    - recommended_action: the action the system recommends
    - target_url: the URL that was being monitored
    - service_name: the target's display name
    - timestamp: when the request was made
    - signature: HMAC-SHA256 of the payload for authenticity verification

    The remote system is expected to:
    1. Verify the signature using the shared secret
    2. Perform the actual remediation action
    3. Return HTTP 200 on success, non-200 on failure
    4. Include a JSON body with {"success": true/false, "message": "..."}
    """

    def __init__(self, webhook_url: str, secret: str, timeout: float = WEBHOOK_TIMEOUT_SECONDS):
        self.webhook_url = webhook_url
        self.secret = secret
        self.timeout = timeout

    async def execute(
        self,
        incident: Incident,
        recommended_action: str,
    ) -> RemediationResult:
        """Call the webhook with a signed remediation request.

        Returns a RemediationResult with success=True only if:
        1. The webhook returned HTTP 200
        2. The response body contained {"success": true}

        Never claims success merely because the webhook was called.
        """
        payload = {
            "incident_id": incident.id,
            "root_cause": incident.report.root_cause if incident.report else "unknown",
            "recommended_action": recommended_action,
            "target_url": incident.target_url,
            "service_name": incident.service_name,
            "source": incident.source,
            "severity": incident.severity.value if incident.severity else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Sign the payload
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        signature = hmac.new(self.secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-IRAS-Signature": f"sha256={signature}",
            "X-IRAS-Incident-Id": incident.id,
        }

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.webhook_url, json=payload, headers=headers)
            latency_ms = (time.monotonic() - start) * 1000

            # Parse response
            try:
                response_body = resp.json()
            except Exception:
                response_body = {}

            success = resp.status_code == 200 and response_body.get("success", False)
            message = response_body.get("message", f"Webhook returned {resp.status_code}")

            if success:
                logger.info(
                    "Webhook remediation succeeded for incident %s: %s",
                    incident.id, message,
                )
            else:
                logger.warning(
                    "Webhook remediation failed for incident %s: %s (HTTP %d)",
                    incident.id, message, resp.status_code,
                )

            return RemediationResult(
                action="webhook_remediation",
                success=success,
                message=message,
                before_state={
                    "webhook_url": self.webhook_url,
                    "latency_ms": round(latency_ms, 1),
                    "http_status": resp.status_code,
                },
                after_state=response_body,
            )

        except httpx.TimeoutException:
            latency_ms = (time.monotonic() - start) * 1000
            logger.error(
                "Webhook remediation timed out for incident %s after %.0fms",
                incident.id, latency_ms,
            )
            return RemediationResult(
                action="webhook_remediation",
                success=False,
                message=f"Webhook timed out after {self.timeout}s",
                before_state={"webhook_url": self.webhook_url, "latency_ms": round(latency_ms, 1)},
                after_state={},
            )

        except Exception as e:
            latency_ms = (time.monotonic() - start) * 1000
            logger.error(
                "Webhook remediation error for incident %s: %s",
                incident.id, e,
            )
            return RemediationResult(
                action="webhook_remediation",
                success=False,
                message=f"Webhook call failed: {e}",
                before_state={"webhook_url": self.webhook_url, "latency_ms": round(latency_ms, 1)},
                after_state={},
            )


def verify_webhook_signature(payload_bytes: bytes, signature: str, secret: str) -> bool:
    """Verify an incoming webhook signature.

    Used by the remote system to verify that a remediation request
    actually came from IRAS and wasn't tampered with.
    """
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
