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

from flask import Flask, jsonify

app = Flask(__name__)

VERSION = os.environ.get("SERVICE_VERSION", "v2.4.0")
FORCE_UNHEALTHY = os.environ.get("FORCE_UNHEALTHY", "false").lower() == "true"


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
    latency = random.uniform(30, 80)
    time.sleep(latency / 1000)
    return jsonify({
        "result": "ok",
        "version": VERSION,
        "latency_ms": round(latency, 1),
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
    app.run(host="0.0.0.0", port=port)
