"""Mock payment service for Bad Deployment scenario.

Healthy mode (default):
    /health  → 200  {"status": "healthy", "version": VERSION}
    /pay     → 200  {"result": "ok", "latency_ms": ~50}

Bad mode (FORCE_UNHEALTHY=true):
    /health  → 500  {"status": "unhealthy", "error": "NullPointerException"}
    /pay     → 500  {"error": "internal server error", "latency_ms": ~2400}
"""

import os
import random
import time
import urllib.request
import urllib.error
import json

from flask import Flask, jsonify

app = Flask(__name__)

VERSION = os.environ.get("SERVICE_VERSION", "v2.4.0")
FORCE_UNHEALTHY = os.environ.get("FORCE_UNHEALTHY", "false").lower() == "true"
RPC_URL = os.environ.get("RPC_URL", None)


@app.route("/health")
def health():
    if FORCE_UNHEALTHY:
        return jsonify({
            "status": "unhealthy",
            "version": VERSION,
            "error": "NullPointerException in PaymentHandler.process",
        }), 500
    return jsonify({"status": "healthy", "version": VERSION}), 200


@app.route("/pay", methods=["POST", "GET"])
def pay():
    if FORCE_UNHEALTHY:
        # Simulate high latency on bad deployment
        time.sleep(random.uniform(1.5, 3.0))
        return jsonify({
            "error": "internal server error",
            "version": VERSION,
            "latency_ms": 2400,
        }), 500

    # Healthy response with normal latency
    start_time = time.time()
    
    # RPC Integration
    rpc_data = None
    if RPC_URL:
        try:
            # Short timeout to simulate dependency failure if toxic is injected
            req = urllib.request.Request(RPC_URL)
            with urllib.request.urlopen(req, timeout=2.0) as response:
                if response.status == 200:
                    rpc_data = json.loads(response.read().decode())
                else:
                    return jsonify({"error": "rpc_failed", "status_code": response.status}), 502
        except urllib.error.URLError as e:
            # If the proxy is disabled (circuit break), connection is refused immediately
            if isinstance(e.reason, ConnectionRefusedError):
                # Successful circuit break fallback
                rpc_data = {"status": "ok", "provider": "fallback_cache"}
            else:
                # Timeout / outage
                return jsonify({"error": "rpc_timeout_or_unreachable", "details": str(e)}), 504
        except Exception as e:
            return jsonify({"error": "rpc_error", "details": str(e)}), 500
    else:
        # Default mock latency if no RPC_URL
        time.sleep(random.uniform(0.03, 0.08))

    latency_ms = (time.time() - start_time) * 1000
    return jsonify({
        "result": "ok",
        "version": VERSION,
        "latency_ms": round(latency_ms, 1),
        "rpc_data": rpc_data
    }), 200


@app.route("/")
def root():
    return jsonify({
        "service": "payment-service",
        "version": VERSION,
        "healthy": not FORCE_UNHEALTHY,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
