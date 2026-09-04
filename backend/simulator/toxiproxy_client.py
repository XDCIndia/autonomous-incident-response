import httpx
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class ToxiproxyClient:
    """Client for communicating with the Toxiproxy REST API."""

    def __init__(self, api_url: str = "http://toxiproxy:8474"):
        self.api_url = api_url.rstrip("/")
        self.client = httpx.Client(timeout=5.0)

    def reset(self):
        """Enable all proxies and remove all active toxics."""
        try:
            resp = self.client.post(f"{self.api_url}/reset")
            resp.raise_for_status()
            logger.info("Toxiproxy reset successful")
        except Exception as e:
            logger.error("Failed to reset toxiproxy: %s", e)

    def create_proxy(self, name: str, listen: str, upstream: str, enabled: bool = True) -> Optional[Dict[str, Any]]:
        """Create a new proxy."""
        payload = {
            "name": name,
            "listen": listen,
            "upstream": upstream,
            "enabled": enabled
        }
        try:
            resp = self.client.post(f"{self.api_url}/proxies", json=payload)
            if resp.status_code == 409:
                logger.info("Proxy %s already exists", name)
                return self.get_proxy(name)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("Failed to create proxy %s: %s", name, e)
            return None

    def get_proxy(self, name: str) -> Optional[Dict[str, Any]]:
        """Get proxy configuration."""
        try:
            resp = self.client.get(f"{self.api_url}/proxies/{name}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("Failed to get proxy %s: %s", name, e)
            return None

    def update_proxy(self, name: str, enabled: Optional[bool] = None, upstream: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update an existing proxy (e.g. enable/disable, change upstream)."""
        proxy = self.get_proxy(name)
        if not proxy:
            return None

        if enabled is not None:
            proxy["enabled"] = enabled
        if upstream is not None:
            proxy["upstream"] = upstream

        try:
            resp = self.client.post(f"{self.api_url}/proxies/{name}", json=proxy)
            resp.raise_for_status()
            logger.info("Updated proxy %s (enabled=%s, upstream=%s)", name, enabled, upstream)
            return resp.json()
        except Exception as e:
            logger.error("Failed to update proxy %s: %s", name, e)
            return None

    def add_toxic(self, proxy_name: str, toxic_name: str, toxic_type: str, toxicity: float = 1.0, attributes: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """Add a toxic to a proxy."""
        payload = {
            "name": toxic_name,
            "type": toxic_type,
            "toxicity": toxicity,
            "attributes": attributes or {}
        }
        try:
            resp = self.client.post(f"{self.api_url}/proxies/{proxy_name}/toxics", json=payload)
            if resp.status_code == 409:
                logger.warning("Toxic %s already exists on proxy %s", toxic_name, proxy_name)
                return payload
            resp.raise_for_status()
            logger.info("Added toxic %s to proxy %s", toxic_name, proxy_name)
            return resp.json()
        except Exception as e:
            logger.error("Failed to add toxic %s to proxy %s: %s", toxic_name, proxy_name, e)
            return None

    def remove_toxic(self, proxy_name: str, toxic_name: str) -> bool:
        """Remove a toxic from a proxy."""
        try:
            resp = self.client.delete(f"{self.api_url}/proxies/{proxy_name}/toxics/{toxic_name}")
            if resp.status_code == 404:
                return True
            resp.raise_for_status()
            logger.info("Removed toxic %s from proxy %s", toxic_name, proxy_name)
            return True
        except Exception as e:
            logger.error("Failed to remove toxic %s from proxy %s: %s", toxic_name, proxy_name, e)
            return False
