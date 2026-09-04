import os
from flask import Flask, jsonify

app = Flask(__name__)
PROVIDER = os.environ.get("PROVIDER", "primary")

@app.route("/process", methods=["POST", "GET"])
def process():
    return jsonify({
        "status": "ok",
        "provider": PROVIDER
    }), 200

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "provider": PROVIDER}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
