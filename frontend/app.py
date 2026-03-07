"""
Frontend Flask Application
- HTML 템플릿 서빙: 각 페이지(시험설정/퀴즈/결과/리뷰/이력)를 렌더링
- /api/* 프록시: 브라우저의 API 호출을 백엔드로 포워딩 (동일 오리진 처리용)
"""

import os

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)

# ──────────────────────────────────────────────────────────
# 백엔드 URL 설정
# Docker: BACKEND_URL=http://backend:5000
# Kubernetes (Gateway 없음): BACKEND_URL=http://backend-service.hc-quiz-bank.svc.cluster.local:5000
# 로컬: http://localhost:5000 (기본값)
# 연결 실패 시 BACKEND_CANDIDATES 순서대로 fallback 시도
# ──────────────────────────────────────────────────────────
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5000").rstrip("/")
# Docker 환경에서는 BACKEND_URL 하나만 사용
# 로컬 직접 실행 시에는 localhost fallback 포함
_extra = []
if "localhost" not in BACKEND_URL and "127.0.0.1" not in BACKEND_URL:
    _extra = ["http://localhost:5000"]
BACKEND_CANDIDATES = list(dict.fromkeys([BACKEND_URL] + _extra))  # 중복 제거
PROXY_TIMEOUT = int(os.getenv("FRONTEND_PROXY_TIMEOUT", "300"))
PROXY_CONNECT_TIMEOUT = int(os.getenv("FRONTEND_PROXY_CONNECT_TIMEOUT", "5"))


# ──────────────────────────────────────────────────────────
# 페이지 라우트 - HTML 템플릿 렌더링
# ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    """시험 설정 페이지: 이름 / 문제 수 / 카테고리 선택"""
    return render_template("index.html")


@app.route("/quiz/<category>")
def quiz(category):
    """퀴즈 진행 페이지: 문제 표시 및 보기 선택 (category=all이면 전체 혼합 모드)"""
    return render_template("quiz.html")


@app.route("/result")
def result():
    """시험 결과 페이지: 정답률 / 정답 수 / 오답 수 표시"""
    return render_template("result.html")


@app.route("/review")
def review():
    """문제 다시보기 페이지: 정답/오답 표시 + 해설 제공"""
    return render_template("review.html")


@app.route("/history")
def history():
    """내 풀이 이력 페이지: 사용자 이름으로 시도 이력 조회"""
    return render_template("history.html")


# ──────────────────────────────────────────────────────────
# /api/* 프록시 라우트
# 브라우저 → 프론트(8080) → 백엔드(5000) 로 요청을 투명하게 전달
# BACKEND_CANDIDATES 순서로 연결 시도, 모두 실패 시 503 반환
# Gateway 환경에서는 이 프록시가 쓰이지 않고 Gateway가 직접 backend-service로 라우팅함
# ──────────────────────────────────────────────────────────
# [API 프록시] GET/POST/PUT/PATCH/DELETE 모든 메서드 지원, JSON/raw 바디 자동 감지
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
                timeout=(PROXY_CONNECT_TIMEOUT, PROXY_TIMEOUT),
                **req_kwargs,
            )
            return Response(
                response=resp.content,
                status=resp.status_code,
                content_type=resp.headers.get("Content-Type", "application/json"),
            )
        except requests.exceptions.ConnectionError:
            continue  # 다음 candidate로 시도
        except requests.exceptions.Timeout:
            return jsonify({"error": "backend timeout", "timeout_sec": PROXY_TIMEOUT, "attempted_backends": attempted}), 504
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # 모든 candidate 연결 실패 → backend가 아직 기동 중이거나 네트워크 문제
    return jsonify({
        "error": "backend connection failed",
        "attempted_backends": attempted,
        "hint": "백엔드가 아직 시작 중일 수 있습니다. 잠시 후 새로고침 해주세요."
    }), 503


@app.route("/health")
def health():
    """프론트엔드 헬스체크 엔드포인트 (Kubernetes readinessProbe 용)"""
    return jsonify({"status": "ok", "service": "frontend"}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)