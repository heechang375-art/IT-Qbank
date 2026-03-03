"""
Frontend Flask Application
- Serve HTML templates
- Proxy /api/* requests to backend API
"""

import os

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)

# Local-first default. In docker, set BACKEND_URL=http://backend:5000
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5000")
BACKEND_CANDIDATES = [BACKEND_URL, "http://localhost:5000", "http://127.0.0.1:5000", "http://backend:5000"]
PROXY_TIMEOUT = int(os.getenv("FRONTEND_PROXY_TIMEOUT", "75"))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/quiz/<category>")
def quiz(category):
    return render_template("quiz.html")


@app.route("/result")
def result():
    return render_template("result.html")


@app.route("/review")
def review():
    return render_template("review.html")


@app.route("/history")
def history():
    return render_template("history.html")


@app.route("/api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def proxy_api(path):
    params = request.args.to_dict()
    headers = {k: v for k, v in request.headers if k.lower() not in ("host", "content-length")}

    req_json = request.get_json(silent=True)
    if req_json is not None:
        req_kwargs = {"json": req_json}
    else:
        raw = request.get_data()
        req_kwargs = {"data": raw} if raw else {}

    attempted = []
    deduped = []
    for candidate in BACKEND_CANDIDATES:
        if candidate and candidate not in deduped:
            deduped.append(candidate)

    for base in deduped:
        attempted.append(base)
        url = f"{base}/api/{path}"
        try:
            resp = requests.request(
                method=request.method,
                url=url,
                params=params,
                headers=headers,
                timeout=(5, PROXY_TIMEOUT),
                **req_kwargs,
            )
            return Response(
                response=resp.content,
                status=resp.status_code,
                content_type=resp.headers.get("Content-Type", "application/json"),
            )
        except requests.exceptions.ConnectionError:
            continue
        except requests.exceptions.Timeout:
            return jsonify({"error": "backend timeout", "timeout_sec": PROXY_TIMEOUT, "attempted_backends": attempted}), 504
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return jsonify({"error": "backend connection failed", "attempted_backends": attempted}), 503


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "frontend"}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
