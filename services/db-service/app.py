import os
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/query", methods=["POST", "GET"])
def query():
    return jsonify({
        "status": "ok",
        "rows_returned": 1
    }), 200

@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
